"""SQLite WAL 的校验与恢复（针对 SQLCipher 加密页）。

SQLite 用累计校验和与 salt 认证 WAL 的结构；SQLCipher 独立认证每个加密页。
本模块负责前者并规划恢复边界，页解密委托给 sqlcipher.decrypt_page。
参照 spec §6.5。

铁律：-shm 只用于交叉验证，它出任何问题都不得导致 WAL 恢复失败。
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

from . import sqlcipher as sc

WAL_MAGIC_LE = 0x377F0682
WAL_MAGIC_BE = 0x377F0683
WAL_HEADER_SZ = 32
WAL_FRAME_HEADER_SZ = 24
WAL_VERSION = 3007000
SHM_HEADER_SZ = 48
_MASK = 0xFFFFFFFF


@dataclass(frozen=True)
class WalHeader:
    magic: int
    version: int
    page_size: int
    checkpoint_seq: int
    salt: tuple[int, int]
    big_endian_checksum: bool
    seed: tuple[int, int]
    signature: bytes


@dataclass(frozen=True)
class WalPlan:
    valid: bool
    reason: str
    page_size: int
    frame_count: int
    last_commit_frame: int      # 1-indexed；0 表示没有已提交帧
    commit_size: int            # 该 commit 之后的库页数
    header_signature: bytes
    shm_validated: bool


@dataclass
class WalApplyResult:
    frames_applied: int = 0
    pages_written: int = 0
    corrupt_frames: int = 0
    truncated_to_pages: int = 0
    signature_changed: bool = False
    stopped_early: bool = False
    notes: list[str] = field(default_factory=list)


def checksum(data: bytes, s0: int, s1: int, big: bool) -> tuple[int, int]:
    """SQLite WAL 累计校验和。data 长度必须是 8 的倍数。"""
    if len(data) % 8:
        raise ValueError("WAL checksum input must be 8-byte aligned")
    fmt = ">II" if big else "<II"
    for i in range(0, len(data), 8):
        v0, v1 = struct.unpack_from(fmt, data, i)
        s0 = (s0 + v0 + s1) & _MASK
        s1 = (s1 + v1 + s0) & _MASK
    return s0, s1


def _norm_page_size(encoded: int) -> int:
    return 65536 if encoded == 1 else encoded


def parse_header(buf: bytes) -> WalHeader | None:
    if len(buf) < WAL_HEADER_SZ:
        return None
    magic, version, encoded_size, ckpt = struct.unpack(">III I", buf[:16])
    if magic not in (WAL_MAGIC_LE, WAL_MAGIC_BE):
        return None
    if version != WAL_VERSION:
        return None
    page_size = _norm_page_size(encoded_size)
    if page_size & (page_size - 1) or not (512 <= page_size <= 65536):
        return None
    salt = struct.unpack(">II", buf[16:24])
    stored = struct.unpack(">II", buf[24:32])
    big = magic == WAL_MAGIC_BE
    seed = checksum(buf[:24], 0, 0, big)
    if seed != stored:
        return None
    return WalHeader(magic, version, page_size, ckpt, salt, big, seed,
                     bytes(buf[:WAL_HEADER_SZ]))


def _read_shm_state(shm_path: Path, hdr: WalHeader) -> tuple[int, int] | None:
    """返回 (mxFrame, nPage)。任何不符都返回 None，调用方降级为仅扫描 WAL。"""
    try:
        with open(shm_path, "rb") as f:
            first = f.read(SHM_HEADER_SZ)
            second = f.read(SHM_HEADER_SZ)
    except OSError:
        return None
    if len(first) < SHM_HEADER_SZ or len(second) < SHM_HEADER_SZ:
        return None
    if first != second:
        return None
    big = bool(first[13])
    if big != hdr.big_endian_checksum:
        return None
    u = ">I" if big else "<I"
    u2 = ">II" if big else "<II"
    try:
        if checksum(first[:40], 0, 0, big) != struct.unpack_from(u2, first, 40):
            return None
        if struct.unpack_from(u2, first, 32) != hdr.salt:
            return None
        mx_frame = struct.unpack_from(u, first, 16)[0]
        n_page = struct.unpack_from(u, first, 20)[0]
    except struct.error:
        return None
    return mx_frame, n_page


def _iter_frames(f, hdr: WalHeader):
    """产出 (frame_no, pgno, dbsize, frame_header_bytes, page_bytes, s0, s1)。

    遇到首个无效帧即停止产出（含尾部不完整帧）。
    """
    s0, s1 = hdr.seed
    frame_no = 0
    while True:
        fh = f.read(WAL_FRAME_HEADER_SZ)
        if len(fh) < WAL_FRAME_HEADER_SZ:
            return
        page = f.read(hdr.page_size)
        if len(page) < hdr.page_size:
            return                                   # 尾部不完整帧，忽略
        if struct.unpack(">II", fh[8:16]) != hdr.salt:
            return                                   # 已回收的旧帧
        s0, s1 = checksum(fh[:8] + page, s0, s1, hdr.big_endian_checksum)
        if (s0, s1) != struct.unpack(">II", fh[16:24]):
            return
        frame_no += 1
        pgno, dbsize = struct.unpack(">II", fh[:8])
        yield frame_no, pgno, dbsize, fh, page, s0, s1


def inspect_wal(wal_path: Path, shm_path: Path | None = None,
                expected_page_size: int = sc.PAGE_SZ) -> WalPlan:
    wal_path = Path(wal_path)
    if not wal_path.is_file():
        return WalPlan(False, "wal_absent", expected_page_size, 0, 0, 0, b"", False)
    try:
        if wal_path.stat().st_size < WAL_HEADER_SZ:
            return WalPlan(False, "wal_too_small", expected_page_size, 0, 0, 0,
                           b"", False)
        with open(wal_path, "rb") as f:
            hdr_buf = f.read(WAL_HEADER_SZ)
            hdr = parse_header(hdr_buf)
            if hdr is None:
                return WalPlan(False, "wal_header_invalid", expected_page_size,
                               0, 0, 0, b"", False)
            if hdr.page_size != expected_page_size:
                return WalPlan(False, "page_size_mismatch", hdr.page_size,
                               0, 0, 0, hdr.signature, False)
            frame_count = 0
            last_commit = 0
            commit_size = 0
            for frame_no, _pgno, dbsize, *_rest in _iter_frames(f, hdr):
                frame_count = frame_no
                if dbsize != 0:
                    last_commit = frame_no
                    commit_size = dbsize
    except OSError as exc:
        return WalPlan(False, f"wal_read_error:{exc.__class__.__name__}",
                       expected_page_size, 0, 0, 0, b"", False)

    shm_validated = False
    if shm_path is not None and Path(shm_path).is_file():
        state = _read_shm_state(Path(shm_path), hdr)
        # mxFrame 不得小于扫描所得帧数；nPage 为 0（未知）或等于最后
        # commit 的库页数才算自洽。任何不符只降级，绝不影响 WAL 恢复。
        if (state is not None and state[0] >= frame_count
                and state[1] in (0, commit_size)):
            shm_validated = True

    return WalPlan(True, "ok", hdr.page_size, frame_count, last_commit,
                   commit_size, hdr.signature, shm_validated)


def apply_wal(params: sc.CipherParams, wal_path: Path, db_path: Path,
              plan: WalPlan) -> WalApplyResult:
    """把已提交帧解密写回 db_path，末尾截断到实际到达的最后 commit 的页数。

    两遍：pass 1 只校验帧结构，确定可应用范围内**实际到达的 commit 帧**
    （replay_to）及其 dbsize；pass 2 用同一个迭代器逐帧重新校验（消除两遍
    之间 WAL 被 QQ 重写后按旧计数回放的 TOCTOU），先解密到内存，只在
    commit 帧把整个事务一次性落盘。两遍之间复查 WAL 头签名。

    原子性边界是 commit 帧（R19）：未提交事务的页绝不写入 db——无论
    pass 2 因页 HMAC 失败、pgno==0 还是结构退化而中止，输出都停留在
    最近一个已 flush 的 commit 上；可应用范围内没有 commit 帧则库保持
    原样不动。

    pgno == 0 是真损坏（会让 seek 落到负偏移，Windows 上抛 OSError）→
    硬中止并计入 corrupt_frames。pgno > plan.commit_size 是合法形态
    （事务内先追加页、commit 前又释放，SQLite 会降低 commit 帧的 nSize；
    walDecodeFrame 并不把高页号当损坏）→ 只跳过该帧、不计损坏、不中止；
    若它恰是 commit 帧，其提交语义仍然生效（照常 flush 本事务并记录
    截断长度），随后继续应用后续帧（R18）。
    """
    res = WalApplyResult()
    if not plan.valid:
        res.notes.append(f"skipped: {plan.reason}")
        return res
    if plan.last_commit_frame == 0:
        res.notes.append("no committed frame; main db left untouched")
        return res

    wal_path, db_path = Path(wal_path), Path(db_path)
    with open(wal_path, "rb") as f:
        if f.read(WAL_HEADER_SZ) != plan.header_signature:
            res.signature_changed = True
            res.notes.append("wal header changed between inspect and apply")
            return res
        f.seek(0)
        hdr = parse_header(f.read(WAL_HEADER_SZ))
        if hdr is None:
            res.signature_changed = True
            return res

        # pass 1：只校验结构，确定可应用范围内实际到达的 commit 帧
        applicable = 0
        replay_to = 0            # 范围内最后一个 commit 帧的帧号
        replay_dbsize = 0        # 该 commit 的 dbsize（最终截断页数的上界来源）
        for frame_no, _pgno, dbsize, *_r in _iter_frames(f, hdr):
            applicable = frame_no
            if dbsize != 0:
                replay_to = frame_no
                replay_dbsize = dbsize
            if frame_no >= plan.last_commit_frame:
                break
        if applicable < plan.last_commit_frame:
            res.stopped_early = True
            res.notes.append(
                f"structure valid only through frame {applicable}, "
                f"expected {plan.last_commit_frame}; replaying to commit "
                f"frame {replay_to} (dbsize {replay_dbsize})")
        if replay_to == 0:
            # 可应用范围内没有任何 commit 帧：一个字都不写，库保持原样
            res.notes.append(
                "no commit frame within applicable range; db left untouched")
            return res

        # 两遍之间复查头签名：pass 1 期间 QQ 若重写了 WAL，绝不进入写入
        f.seek(0)
        if f.read(WAL_HEADER_SZ) != plan.header_signature:
            res.signature_changed = True
            res.notes.append("wal header changed during pass 1")
            return res

        # pass 2：逐帧重新校验结构后解密；事务缓存在内存，commit 帧才落盘
        f.seek(WAL_HEADER_SZ)
        target_pages = 0                 # 实际到达的最后 commit 的 dbsize
        pending: dict[int, bytes] = {}   # 未提交事务：pgno -> 明文页
        pending_n = 0                    # 未提交事务包含的帧数
        aborted = False                  # pass 2 是否因损坏而中止
        saw_replay_to = False

        def flush(db) -> None:
            nonlocal pending_n
            for p, data in pending.items():
                db.seek((p - 1) * hdr.page_size)
                db.write(data)
            res.frames_applied += pending_n
            res.pages_written += len(pending)
            pending.clear()
            pending_n = 0

        with open(db_path, "r+b") as db:
            for frame_no, pgno, dbsize, _fh, page, *_r in _iter_frames(f, hdr):
                if frame_no > replay_to:
                    if pending_n:
                        res.notes.append(
                            f"discarded {pending_n} uncommitted frame(s) "
                            f"beyond replay limit {replay_to}")
                        pending.clear()
                        pending_n = 0
                    break
                if frame_no == replay_to:
                    saw_replay_to = True
                # 页号守卫必须在任何 seek 之前。pgno==0 会让 (pgno-1)*page_size
                # 变成负偏移（Windows 上抛 OSError）——真损坏，硬中止；
                # pgno > plan.commit_size 是 commit 收缩库的合法形态（R18），
                # 只跳过该帧，后续帧照常应用。
                if pgno == 0:
                    res.corrupt_frames += 1
                    res.stopped_early = True
                    res.notes.append(
                        f"frame {frame_no}: pgno 0 is invalid; aborting replay")
                    pending.clear()
                    pending_n = 0
                    aborted = True
                    break
                if pgno > plan.commit_size:
                    res.notes.append(
                        f"frame {frame_no}: pgno {pgno} beyond final db size "
                        f"{plan.commit_size} (page freed before commit); "
                        f"frame skipped")
                    if dbsize != 0:
                        # 提交语义仍然生效：本事务已提交，只是这一页不在
                        # 最终库范围内。照常 flush 并记录截断长度。
                        flush(db)
                        target_pages = dbsize
                    continue
                plain = sc.decrypt_page(params, page, pgno)
                if plain is None:
                    res.corrupt_frames += 1
                    res.stopped_early = True
                    res.notes.append(f"page HMAC failed at frame {frame_no}")
                    # 未提交事务的页全部丢弃——绝不留在输出里（R19）
                    pending.clear()
                    pending_n = 0
                    aborted = True
                    break
                # 页 1 第 20 字节（SQLite reserved bytes）保留 WAL 里的原值：
                # 它不等于密文尾 reserve（真机实测 80 vs 48，R34），改写会
                # 让整库 quick_check 报 Fragmentation。
                pending[pgno] = plain
                pending_n += 1
                if dbsize != 0:
                    flush(db)
                    target_pages = dbsize
            if not aborted and not saw_replay_to:
                # pass 2 的结构复校验提前终止：WAL 在两遍之间被改写了
                res.stopped_early = True
                res.notes.append(
                    "wal frames failed revalidation during replay; "
                    "uncommitted tail discarded")
            if pending_n:
                # 理论上到不了这里（replay_to 是 commit 帧），防御性丢弃
                res.notes.append(
                    f"discarded {pending_n} frame(s) of an uncommitted "
                    f"transaction at end of replay")

        if target_pages:
            with open(db_path, "r+b") as db:
                db.truncate(target_pages * hdr.page_size)
            res.truncated_to_pages = target_pages
    return res
