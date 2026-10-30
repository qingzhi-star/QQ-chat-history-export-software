"""编排 snapshot → sqlcipher → wal，流式产出标准明文 SQLite。参照 spec §6.7。

三个不变量：

1. **不产生「剥头中间文件」**：从快照偏移 1024 处流式读，写到明文文件偏移 0，
   省掉一次整库复制（主库可达 369 MB）。
2. **损坏页写零页而非跳过写入**：否则后续所有页偏移错位、整库不可读。
3. **原子输出**：先写 `<dst>.<pid>.<8位随机>.partial`（与目标同目录以保证同卷），
   成功后 `os.replace`；失败路径清理 partial，已存在的旧产物在成功前保持不变。

三态状态（修正案 A §8.1）：

    complete  全部页认证通过、WAL 完整回放、quick_check == ok
    lossy     产物可用但有缺失：损坏页 / WAL 存在但无效 / 回放不完整 /
              WAL 在 inspect 与 apply 之间被改写 / 快照一致性 unverified
    failed    quick_check != ok 或产物页 1 缺 SQLite 魔数：删除产物，但**仍返回
              结果对象**供诊断

`failed` 时不得只追加 warning 就返回成功（修正案 A §0.1）。
"""
from __future__ import annotations

import contextlib
import os
import secrets
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import snapshot as snap
from . import sqlcipher as sc
from . import wal
from .discover import QQAccount

PLAIN_SUFFIX = ".decrypted.db"
_PARTIAL_SUFFIX = ".partial"
_MAGIC_LEN = 16


class DecryptError(RuntimeError):
    """解密失败。消息不得包含口令或数据库绝对路径。"""


@dataclass
class DecryptResult:
    plaintext_db: Path
    page_count: int = 0
    corrupt_pages: int = 0
    cipher_fingerprint: str = ""
    lock_page_shifted: bool = False
    wal_frames: int = 0
    wal_last_commit_frame: int = 0
    wal_commit_size: int = 0
    wal_applied_frames: int = 0
    wal_corrupt_frames: int = 0
    wal_signature_changed: bool = False
    wal_truncated: bool = False
    wal_reason: str = ""
    shm_validated: bool = False
    integrity_check: str = ""
    tables: list[str] = field(default_factory=list)
    duration_s: float = 0.0
    warnings: list[str] = field(default_factory=list)
    # 以下为修正案 A 追加字段（不在原计划字段集内，追加在末尾）
    status: str = ""
    snapshot_consistency: str = ""
    wal_stopped_early: bool = False


def integrity_probe(db_path: Path) -> tuple[str, list[str]]:
    """用只读 URI 打开明文库，返回 (quick_check 结论, 表名列表)。

    用 quick_check 而非 integrity_check：后者校验全部索引，369 MB 库上过慢。
    mode=ro 保证不写、不生成 journal。
    """
    p = Path(db_path)
    if not p.is_file():
        return "open_failed:missing", []
    uri = p.resolve().as_uri() + "?mode=ro"
    try:
        con = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        return f"open_failed:{exc.__class__.__name__}", []
    try:
        try:
            row = con.execute("PRAGMA quick_check").fetchone()
            verdict = str(row[0]) if row else "no_result"
        except sqlite3.Error as exc:
            verdict = f"error:{exc.__class__.__name__}"
        try:
            tables = [str(r[0]) for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        except sqlite3.Error:
            tables = []
        return verdict, tables
    finally:
        con.close()


def _partial_path(dst: Path) -> Path:
    """同目录、唯一名：`<dst>.<pid>.<8位随机>.partial`（修正案 A §8.2）。"""
    token = f"{os.getpid()}.{secrets.token_hex(4)}"
    return dst.with_name(f"{dst.name}.{token}{_PARTIAL_SUFFIX}")


def decrypt_pages(src_db: Path, dst_db: Path, params: sc.CipherParams, *,
                  lock_page_no: int = sc.LOCK_PAGE_NO) -> tuple[int, int, bool]:
    """流式解密。返回 (page_count, corrupt_pages, lock_page_shifted)。

    **以页 1 头里的 database size 为准**：真机观测到文件会比逻辑页数大
    （尾随旧密文/零页，SQLite 本就不会读它们）；若按文件大小解密，这些
    尾页会全部记为 HMAC 失败，把好库误判成 lossy。
    """
    page_count = 0
    corrupt = 0
    shifted = False
    zero_page = b"\x00" * sc.PAGE_SZ
    dst_db = Path(dst_db)
    dst_db.parent.mkdir(parents=True, exist_ok=True)
    total_pages = max(0, ((Path(src_db).stat().st_size - sc.WRAPPER_SZ
                           + sc.PAGE_SZ - 1) // sc.PAGE_SZ))
    limit = total_pages

    with open(src_db, "rb") as fin, open(dst_db, "wb") as fout:
        fin.seek(sc.WRAPPER_SZ)
        while page_count < limit:
            page = fin.read(sc.PAGE_SZ)
            if not page:
                break
            page_count += 1
            if len(page) < sc.PAGE_SZ:
                # 尾部截断：补零到整页，否则偏移错位
                corrupt += 1
                fout.write(page + b"\x00" * (sc.PAGE_SZ - len(page)))
                break
            if page_count == lock_page_no and page == zero_page:
                shifted = True
                fout.write(page)
                continue
            plain = sc.decrypt_page(params, page, page_count)
            if plain is None:
                corrupt += 1
                fout.write(zero_page)        # 写零页而不是跳过：保持页偏移
                continue
            # 页 1 第 20 字节（SQLite reserved bytes）必须保留解密出的原值。
            # 真机实测 QQ 9.9.36 是 80，而密文尾 reserve 是 48——两者不是同一个
            # 概念；改写成 48 会把 SQLite 的可用页尾从 4016 抬到 4048，整库
            # quick_check 报 «Fragmentation of 32 bytes reported as 0»（R34）。
            fout.write(plain)
            if page_count == 1:
                hdr = _header_pages_from_page(plain)
                if hdr and hdr < limit:
                    limit = hdr
    return page_count, corrupt, shifted


def _header_pages_from_page(plain: bytes) -> int | None:
    if len(plain) < 32 or not plain.startswith(sc.SQLITE_MAGIC):
        return None
    page_size = int.from_bytes(plain[16:18], "big")
    pages = int.from_bytes(plain[28:32], "big")
    if page_size != sc.PAGE_SZ or not (0 < pages <= 1 << 30):
        return None
    return pages


def _truncate_to_header(db_path: Path) -> int | None:
    """把产物截断到页 1 头声明的页数；返回该页数（无法判断时 None）。"""
    try:
        with open(db_path, "r+b") as f:
            page1 = f.read(sc.PAGE_SZ)
            hdr = _header_pages_from_page(page1)
            if hdr is None:
                return None
            want = hdr * sc.PAGE_SZ
            size = Path(db_path).stat().st_size
            if want < size:
                f.truncate(want)
            return hdr
    except OSError:
        return None


def _page1_has_magic(db_path: Path) -> bool:
    try:
        with open(db_path, "rb") as f:
            return f.read(_MAGIC_LEN) == sc.SQLITE_MAGIC
    except OSError:
        return False


def _compute_status(*, corrupt_pages: int, wal_invalid: bool,
                    wal_signature_changed: bool, wal_stopped_early: bool,
                    wal_corrupt_frames: int, integrity_check: str,
                    page1_ok: bool, snapshot_consistency: str) -> str:
    """修正案 A §8.1 的三态判定。任何降级条件命中都不得返回 complete。"""
    if integrity_check != "ok" or not page1_ok:
        return "failed"
    if (corrupt_pages > 0 or wal_invalid or wal_signature_changed
            or wal_stopped_early or wal_corrupt_frames > 0
            or snapshot_consistency == "unverified"):
        return "lossy"
    return "complete"


def decrypt_file(src_db: Path, passphrase: bytes, dst_db: Path, *,
                 wal_path: Path | None = None,
                 shm_path: Path | None = None,
                 overwrite: bool = True,
                 snapshot_consistency: str = "") -> DecryptResult:
    """解密单个库文件（含 WAL 恢复）。产物原子替换到 dst_db。

    overwrite=False 且目标已存在时抛 DecryptError，不做任何写入（修正案 A §8.3）。
    """
    started = time.monotonic()
    src_db, dst_db = Path(src_db), Path(dst_db)

    if not overwrite and dst_db.exists():
        raise DecryptError(
            f"目标文件已存在：{dst_db.name}。已指定不覆盖，未做任何写入。")

    head = sc.read_header_and_page1(src_db)
    params = sc.detect_params(passphrase, head)
    if params is None:
        raise DecryptError(
            "口令校验失败：无法用它解密该库的第 1 页。"
            "可能是口令不对，或 QQ 版本改动了加密参数。"
            "请重新执行 `qqexport key scan`，或用 `qqexport key set` 手动写入。")

    partial = _partial_path(dst_db)
    try:
        page_count, corrupt, shifted = decrypt_pages(src_db, partial, params)
        res = DecryptResult(plaintext_db=dst_db, page_count=page_count,
                            corrupt_pages=corrupt,
                            cipher_fingerprint=params.fingerprint,
                            lock_page_shifted=shifted,
                            snapshot_consistency=snapshot_consistency)
        if corrupt:
            res.warnings.append(f"{corrupt} 个页损坏（HMAC 校验失败），已写零页占位")

        wal_invalid = _recover_wal(res, params, partial, wal_path, shm_path)
        hdr_pages = _truncate_to_header(partial)
        if hdr_pages is not None:
            res.page_count = hdr_pages

        res.integrity_check, res.tables = integrity_probe(partial)
        if res.integrity_check != "ok":
            res.warnings.append(f"quick_check 结论：{res.integrity_check}")
        if not res.tables:
            res.warnings.append("sqlite_master 中未发现任何表")

        res.status = _compute_status(
            corrupt_pages=res.corrupt_pages, wal_invalid=wal_invalid,
            wal_signature_changed=res.wal_signature_changed,
            wal_stopped_early=res.wal_stopped_early,
            wal_corrupt_frames=res.wal_corrupt_frames,
            integrity_check=res.integrity_check,
            page1_ok=_page1_has_magic(partial),
            snapshot_consistency=res.snapshot_consistency)
        res.duration_s = time.monotonic() - started

        if res.status == "failed":
            # 仍返回结果对象供诊断，但产物必须删除（修正案 A §8.1）
            partial.unlink(missing_ok=True)
            return res

        os.replace(partial, dst_db)
        return res
    except BaseException:
        with contextlib.suppress(OSError):
            partial.unlink(missing_ok=True)
        raise


def _recover_wal(res: DecryptResult, params: sc.CipherParams, db_path: Path,
                 wal_path: Path | None, shm_path: Path | None) -> bool:
    """回放 WAL 并填充结果字段。返回「WAL 存在但结构无效」标志。"""
    if wal_path is None or not Path(wal_path).is_file():
        res.wal_reason = "wal_absent"
        return False
    plan = wal.inspect_wal(Path(wal_path),
                           Path(shm_path) if shm_path else None,
                           expected_page_size=sc.PAGE_SZ)
    res.wal_frames = plan.frame_count
    res.wal_last_commit_frame = plan.last_commit_frame
    res.wal_commit_size = plan.commit_size
    res.shm_validated = plan.shm_validated
    res.wal_reason = plan.reason
    if not plan.valid:
        res.warnings.append(
            f"WAL 未恢复（{plan.reason}），产物是主库回退版，"
            "只含已 checkpoint 的数据")
        return True

    applied = wal.apply_wal(params, Path(wal_path), db_path, plan)
    res.wal_applied_frames = applied.frames_applied
    res.wal_corrupt_frames = applied.corrupt_frames
    res.wal_signature_changed = applied.signature_changed
    res.wal_stopped_early = bool(applied.stopped_early)
    res.wal_truncated = applied.truncated_to_pages > 0
    if applied.truncated_to_pages:
        res.page_count = applied.truncated_to_pages
    if applied.corrupt_frames:
        res.warnings.append(
            f"{applied.corrupt_frames} 个 WAL 帧页 HMAC 失败，已停止应用后续帧")
    if applied.signature_changed:
        res.warnings.append("WAL 在 inspect 与 apply 之间被改写，本次未应用任何帧")
    if applied.stopped_early:
        res.warnings.append("WAL 提前停止应用：" + "；".join(applied.notes))
    return False


def decrypt_database(account: QQAccount, passphrase: bytes, *, dst_dir: Path,
                     db_name: str = "nt_msg.db",
                     attempts: int = snap.DEFAULT_ATTEMPTS,
                     overwrite: bool = True) -> DecryptResult:
    """快照 + 解密的完整流程。快照失败统一包装成 DecryptError。

    快照副本（db/-wal/-shm）在返回前删除：它们只是本次运行的工作副本，
    不该留在 work 目录里占空间（修正案 A §8.2）。
    """
    dst_dir = Path(dst_dir)
    dst_dir.mkdir(parents=True, exist_ok=True)
    src_db = account.db_dir / db_name
    if not src_db.is_file():
        raise DecryptError(f"源数据库不存在：{db_name}。请确认账号数据目录已选对。")

    try:
        shot = snap.snapshot(src_db, dst_dir,
                             validator=snap.make_validator(passphrase),
                             attempts=attempts)
    except snap.SnapshotError as exc:
        raise DecryptError(str(exc)) from exc

    final = dst_dir / (Path(db_name).stem + PLAIN_SUFFIX)
    try:
        return decrypt_file(shot.db_path, passphrase, final,
                            wal_path=shot.wal_path, shm_path=shot.shm_path,
                            overwrite=overwrite,
                            snapshot_consistency=shot.consistency)
    finally:
        for p in (shot.db_path, shot.wal_path, shot.shm_path):
            if p is not None:
                with contextlib.suppress(OSError):
                    p.unlink(missing_ok=True)
