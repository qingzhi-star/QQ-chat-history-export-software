"""Task 5：尽力获取一致快照（snapshot.py）。

测试全部使用 tmp_path 下的合成文件：不读任何真实 QQ 路径、不调用 discover、
不触碰运行中 QQ 的 -shm（修正案 A §5.3 / R23）。

shm 锁的机械语义在一个与真实 -shm 同样大小（32768 字节）的合成文件上验证。
"""
import builtins
import contextlib
import dataclasses
import os
import time
from pathlib import Path

import pytest

from qqexport.core import snapshot as sn
from qqexport.core import sqlcipher as sc
from tests.helpers import fake_sqlcipher as fake

if os.name == "nt":
    import msvcrt
else:
    msvcrt = None

PASSPHRASE = b"#8FakeKeyForTest"
DB_SALT = bytes(range(16))
AES_KEY, MAC_KEY = sc.derive_keys(PASSPHRASE, DB_SALT, "sha512")
RESERVE = 48
SHM_REAL_SIZE = 32768          # 真实 nt_msg.db-shm 的大小
GARBAGE_WAL = b"W" * 300       # 结构无效的 WAL：头部 magic 就不对

win_only = pytest.mark.skipif(os.name != "nt", reason="msvcrt 字节范围锁仅 Windows")


# ---------------------------------------------------------------- 合成数据

def plain(tag: int) -> bytes:
    """明网页：正文全为 tag，尾部 reserve 区置零（与 decrypt_page 输出同形）。"""
    return bytes([tag]) * (sc.PAGE_SZ - RESERVE) + b"\x00" * RESERVE


def valid_wal_bytes() -> bytes:
    """结构完整的合成 WAL。inspect_wal 只做结构校验，无需口令即可判定 valid。"""
    return fake.build_wal([(2, plain(2), 4)], aes_key=AES_KEY, mac_key=MAC_KEY,
                          mac="sha1", reserve=RESERVE)


def make_source(root: Path, *, n_pages: int = 4, with_wal: bool = True,
                with_shm: bool = True, valid: bool = True,
                shm_size: int = 136, wal_bytes: bytes | None = None) -> Path:
    """在 root 下造一个合成的 QQ 数据目录，返回 nt_msg.db 路径。"""
    db_dir = root / "123456789" / "nt_qq" / "nt_db"
    db_dir.mkdir(parents=True)
    pages = fake.random_plain_pages(n_pages)
    blob = fake.build_encrypted_db(pages, PASSPHRASE, salt=DB_SALT) if valid \
        else b"\x00" * 5120
    db = db_dir / "nt_msg.db"
    db.write_bytes(blob)
    if with_wal:
        (db_dir / "nt_msg.db-wal").write_bytes(
            GARBAGE_WAL if wal_bytes is None else wal_bytes)
    if with_shm:
        (db_dir / "nt_msg.db-shm").write_bytes(b"\x00" * shm_size)
    return db


def family(src_db: Path) -> list[Path]:
    return [src_db.with_name(src_db.name + s) for s in ("", "-wal", "-shm")]


def mtimes(root: Path) -> dict[str, float]:
    return {str(p.relative_to(root)): p.stat().st_mtime_ns / 1e9
            for p in root.rglob("*") if p.is_file()}


def const_cm(value: bool):
    return contextlib.nullcontext(value)


def synthetic_shm(tmp_path: Path, size: int = SHM_REAL_SIZE) -> Path:
    p = tmp_path / "nt_msg.db-shm"
    p.write_bytes(bytes(size))
    return p


def range_is_free(path: Path, off: int, n: int) -> bool:
    """另一句柄能否立刻非阻塞锁住 [off, off+n)。True 表示该区间当前自由。"""
    fd = os.open(path, os.O_RDWR)
    try:
        os.lseek(fd, off, 0)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, n)
    except OSError:
        os.close(fd)
        return False
    try:
        os.lseek(fd, off, 0)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, n)
        return True
    finally:
        os.close(fd)


# ------------------------------------------------------- copy_family 基本行为

def test_copy_family_copies_db_wal_shm(tmp_path):
    src = make_source(tmp_path / "src")
    dst = tmp_path / "work"
    db, w, s = sn.copy_family(src, dst)
    assert db == dst / "nt_msg.db" and db.read_bytes() == src.read_bytes()
    assert w == dst / "nt_msg.db-wal" and w.read_bytes() == GARBAGE_WAL
    assert s == dst / "nt_msg.db-shm" and s.read_bytes() == b"\x00" * 136


def test_copy_family_tolerates_missing_wal_and_shm(tmp_path):
    src = make_source(tmp_path / "src", with_wal=False, with_shm=False)
    db, w, s = sn.copy_family(src, tmp_path / "work")
    assert db.is_file() and w is None and s is None


def test_copy_family_creates_dst_dir(tmp_path):
    src = make_source(tmp_path / "src")
    dst = tmp_path / "deep" / "nested" / "work"
    assert not dst.exists()
    sn.copy_family(src, dst)
    assert dst.is_dir() and (dst / "nt_msg.db").is_file()


def test_copy_family_raises_when_source_db_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        sn.copy_family(tmp_path / "nope.db", tmp_path / "work")


def test_snapshot_reports_missing_source_db_honestly(tmp_path):
    """源库不存在跟「QQ 正在写」是两回事，不得给同一条误导性提示。"""
    with pytest.raises(sn.SnapshotError) as ei:
        sn.snapshot(tmp_path / "nope.db", tmp_path / "work")
    msg = str(ei.value)
    assert "不存在" in msg
    assert "退出 QQ" not in msg
    assert "尽力获取一致快照" in msg


def test_copy_one_streams_in_chunks(tmp_path, monkeypatch):
    """_copy_one 必须分块读：主库 369 MB，绝不能整份进内存。"""
    src = tmp_path / "big.db"
    payload = b"\x00" * (sn.COPY_CHUNK * 2 + 5)
    src.write_bytes(payload)
    dst = tmp_path / "out.db"
    reads: list[int] = []
    real_open = builtins.open

    def spy_open(file, mode="r", *a, **kw):
        fh = real_open(file, mode, *a, **kw)
        m = str(mode)
        if "b" in m and "r" in m and "+" not in m:
            return _ReadSpy(fh, reads)
        return fh

    monkeypatch.setattr(builtins, "open", spy_open)
    n = sn._copy_one(src, dst)
    monkeypatch.undo()

    assert n == len(payload)
    assert dst.read_bytes() == payload
    # 每次只请求 COPY_CHUNK；若整文件读入会出现 -1，若用 shutil 默认块则是 65536
    assert reads == [sn.COPY_CHUNK] * 4, reads
    assert -1 not in reads and all(0 < r <= sn.COPY_CHUNK for r in reads)


class _ReadSpy:
    """包装二进制读句柄，记录每次 read 请求的字节数。"""

    def __init__(self, fh, log):
        self._fh = fh
        self._log = log

    def read(self, n=-1):
        self._log.append(n)
        return self._fh.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return self._fh.__exit__(*exc)

    def __getattr__(self, name):
        return getattr(self._fh, name)


# ------------------------------------------------- 陈旧副本与不完整副本的清理

def test_stale_destination_files_removed_before_copy(tmp_path):
    """§5.4：目标目录里上一轮的旧副本必须先清掉，否则旧 WAL 会配新 DB。"""
    src = make_source(tmp_path / "src", with_wal=False, with_shm=False)
    dst = tmp_path / "work"
    dst.mkdir()
    (dst / "nt_msg.db").write_bytes(b"OLD-STALE-DB")
    (dst / "nt_msg.db-wal").write_bytes(b"OLD-STALE-WAL")
    (dst / "nt_msg.db-shm").write_bytes(b"OLD-STALE-SHM")

    db, w, s = sn.copy_family(src, dst)

    assert db.read_bytes() == src.read_bytes()
    assert w is None and s is None
    assert not (dst / "nt_msg.db-wal").exists(), "旧 -wal 残留会与新 DB 配对"
    assert not (dst / "nt_msg.db-shm").exists(), "旧 -shm 残留"


def test_snapshot_result_has_no_stale_sidecar(tmp_path):
    """snapshot 层面同样不得把陈旧 sidecar 当成快照的一部分返回。"""
    src = make_source(tmp_path / "src", with_wal=False, with_shm=False)
    dst = tmp_path / "work"
    dst.mkdir()
    (dst / "nt_msg.db-wal").write_bytes(b"OLD-STALE-WAL")

    res = sn.snapshot(src, dst, use_shm_lock=False)

    assert res.wal_path is None
    assert not (dst / "nt_msg.db-wal").exists()
    assert res.consistency == "best_effort"      # 无 WAL 是合法状态


def test_failed_copy_leaves_no_partial_files(tmp_path, monkeypatch):
    """§5.4：复制中途失败后目标目录不得留下不完整副本。"""
    src = make_source(tmp_path / "src")
    real = sn._copy_one

    def fail_on_wal(s, d):
        if str(d).endswith("-wal"):
            real(s, d)                            # 先写出部分内容再失败
            raise OSError("simulated copy failure")
        return real(s, d)

    monkeypatch.setattr(sn, "_copy_one", fail_on_wal)
    with pytest.raises(sn.SnapshotError):
        sn.snapshot(src, tmp_path / "work", attempts=2, use_shm_lock=False)

    assert list((tmp_path / "work").glob("nt_msg.db*")) == []


def test_copy_family_cleans_partial_files_when_a_member_copy_fails(tmp_path,
                                                                   monkeypatch):
    """copy_family 自己必须清干净：snapshot 之外还有别的调用方（Task 8）。"""
    src = make_source(tmp_path / "src")
    dst = tmp_path / "work"
    real = sn._copy_one

    def fail_on_wal(s, d):
        if str(d).endswith("-wal"):
            real(s, d)                            # 先写出部分内容再失败
            raise OSError("simulated copy failure")
        return real(s, d)

    monkeypatch.setattr(sn, "_copy_one", fail_on_wal)
    with pytest.raises(OSError):
        sn.copy_family(src, dst)
    assert list(dst.glob("nt_msg.db*")) == []


def test_copy_error_is_retried(tmp_path, monkeypatch):
    src = make_source(tmp_path / "src")
    real = sn._copy_one
    state = {"n": 0}

    def flaky(s, d):
        state["n"] += 1
        if state["n"] == 2:                       # 第一轮的 -wal 复制失败
            raise OSError("simulated copy failure")
        return real(s, d)

    monkeypatch.setattr(sn, "_copy_one", flaky)
    res = sn.snapshot(src, tmp_path / "work", use_shm_lock=False)
    assert res.attempts == 2
    assert state["n"] >= 3


# ------------------------------------------------------- FileIdentity（第 1/3 层）

def test_file_identity_of_missing_file_does_not_raise(tmp_path):
    ident = sn.file_identity(tmp_path / "nope.db")
    assert ident.exists is False
    assert isinstance(ident, sn.FileIdentity)
    assert (ident.size, ident.mtime_ns, ident.dev, ident.ino) == (0, 0, 0, 0)


def test_file_identity_records_dev_ino_size_mtime(tmp_path):
    p = tmp_path / "a.db"
    p.write_bytes(b"X" * 100)
    st = p.stat()
    ident = sn.file_identity(p)
    assert ident.exists is True
    assert (ident.size, ident.mtime_ns, ident.dev, ident.ino) == (
        st.st_size, st.st_mtime_ns, st.st_dev, st.st_ino)


def test_file_identity_detects_replacement_with_same_size_and_mtime(tmp_path):
    """R22：Windows 上 st_ino 就是文件索引，替换文件必须被识别出来。"""
    p = tmp_path / "a.db"
    p.write_bytes(b"X" * 100)
    st = p.stat()
    first = sn.file_identity(p)

    p.unlink()
    p.write_bytes(b"X" * 100)                    # 内容与大小完全相同
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))   # mtime 也还原

    second = sn.file_identity(p)
    assert first != second
    assert first.ino != second.ino, "仅靠 size/mtime 无法识别文件被替换"


def test_snapshot_retries_when_source_changes_during_copy(tmp_path, monkeypatch):
    """核心用例：副本内容完全可用，只有 identity 比对能发现复制期间源变了。"""
    src = make_source(tmp_path / "src")
    wal = src.with_name(src.name + "-wal")
    real = sn.copy_family
    state = {"raced": 0}

    def racy(src_db, dst_dir, **kw):
        out = real(src_db, dst_dir, **kw)
        if state["raced"] == 0:
            state["raced"] += 1
            with open(wal, "ab") as f:            # 模拟 QQ 在复制期间追加 WAL 帧
                f.write(b"\x00" * 8)
        return out

    monkeypatch.setattr(sn, "copy_family", racy)
    res = sn.snapshot(src, tmp_path / "work", use_shm_lock=False)

    assert state["raced"] == 1
    assert res.attempts == 2


def test_snapshot_retries_when_wal_vanishes_during_copy(tmp_path, monkeypatch):
    """§5.4：复制过程中 WAL 被 checkpoint 后删除 → exists 变化应被捕获。"""
    src = make_source(tmp_path / "src")
    wal = src.with_name(src.name + "-wal")
    real = sn.copy_family
    state = {"n": 0}

    def racy(src_db, dst_dir, **kw):
        state["n"] += 1
        if state["n"] == 1:
            wal.unlink()
        return real(src_db, dst_dir, **kw)

    monkeypatch.setattr(sn, "copy_family", racy)
    res = sn.snapshot(src, tmp_path / "work", use_shm_lock=False)

    assert res.attempts == 2 and res.wal_path is None


def test_snapshot_raises_when_source_never_stops_changing(tmp_path, monkeypatch):
    src = make_source(tmp_path / "src")
    wal = src.with_name(src.name + "-wal")
    real = sn.copy_family

    def always_racy(src_db, dst_dir, **kw):
        out = real(src_db, dst_dir, **kw)
        with open(wal, "ab") as f:
            f.write(b"\x00")
        return out

    monkeypatch.setattr(sn, "copy_family", always_racy)
    with pytest.raises(sn.SnapshotError):
        sn.snapshot(src, tmp_path / "work", attempts=3, use_shm_lock=False)
    assert list((tmp_path / "work").glob("nt_msg.db*")) == []


# -------------------------------------------------- 主库内容校验（第 5 层之一）

def test_default_validator_accepts_wellformed_db(tmp_path):
    src = make_source(tmp_path / "src")
    assert sn.make_validator(PASSPHRASE)(src) is True


def test_default_validator_rejects_non_qq_file(tmp_path):
    bogus = tmp_path / "bogus.db"
    bogus.write_bytes(b"SQLite format 3\x00" + b"\x00" * 4080)   # 无 1024 头
    assert sn.make_validator(PASSPHRASE)(bogus) is False
    assert sn.make_validator(None)(bogus) is False


def test_default_validator_without_passphrase_checks_shape_only(tmp_path):
    src = make_source(tmp_path / "src")
    assert sn.make_validator(None)(src) is True

    src.write_bytes(b"\x00" * 4096)              # 太短：连 1024+4096 都读不满
    assert sn.make_validator(None)(src) is False

    # 8192 字节：够长、page-1 salt 位置也非零，长度检查与 salt 检查都拦不住，
    # 只有 size % 4096 == 1024 这条能挡下它
    src.write_bytes(b"\x00" * sc.WRAPPER_SZ + b"\x11" * (sc.PAGE_SZ + 1024)
                    + b"\x00" * 2048)
    assert src.stat().st_size == 8192
    assert sn.make_validator(None)(src) is False


def test_default_validator_rejects_header_only_file(tmp_path):
    """只有 1024 字节头、一页都没有。

    这种文件 size % 4096 == 1024 成立，大小检查拦不住它；而 head[1024:5120]
    是空串，空串的 [:16] 也不等于 16 个零字节，salt 检查同样拦不住。唯一能
    挡下它的是「必须读满 1024+4096」这条长度检查——删掉就会把空库判成好库。
    """
    p = tmp_path / "head-only.db"
    p.write_bytes(b"\x00" * sc.WRAPPER_SZ)
    assert p.stat().st_size % sc.PAGE_SZ == sc.WRAPPER_SZ   # 大小检查看不出问题
    assert sn.make_validator(None)(p) is False
    assert sn.make_validator(PASSPHRASE)(p) is False


def test_default_validator_rejects_zero_salt(tmp_path):
    """page-1 salt 全零 → 不是 QQ NT 加密库。"""
    src = make_source(tmp_path / "src", valid=False)
    assert sn.make_validator(None)(src) is False
    assert sn.make_validator(PASSPHRASE)(src) is False


def test_default_validator_rejects_wrong_passphrase(tmp_path):
    src = make_source(tmp_path / "src")
    assert sn.make_validator(b"#8WrongKeyXXXXXX")(src) is False


def test_default_validator_handles_missing_file(tmp_path):
    assert sn.make_validator(None)(tmp_path / "nope.db") is False


# ------------------------------------------------ WAL 交叉校验（第 5 层之二）

def test_wal_validation_runs_against_copies_not_source(tmp_path, monkeypatch):
    """§5.1 第 5 步校验的是副本；拿源文件校验等于什么都没验。"""
    src = make_source(tmp_path / "src", wal_bytes=valid_wal_bytes())
    dst = tmp_path / "work"
    seen: list[tuple[Path, Path | None]] = []
    real = sn.wal.inspect_wal

    def spy(wal_path, shm_path=None, **kw):
        seen.append((Path(wal_path), None if shm_path is None else Path(shm_path)))
        return real(wal_path, shm_path, **kw)

    monkeypatch.setattr(sn.wal, "inspect_wal", spy)
    sn.snapshot(src, dst, use_shm_lock=False)

    assert seen, "从未调用 inspect_wal"
    for w, s in seen:
        assert w.parent == dst and w.name == "nt_msg.db-wal"
        assert s is None or s.parent == dst
        assert w.parent != src.parent


def test_absent_wal_is_a_legitimate_pass(tmp_path):
    src = make_source(tmp_path / "src", with_wal=False, with_shm=False)
    res = sn.snapshot(src, tmp_path / "work", use_shm_lock=False)
    assert res.wal_path is None
    assert res.consistency == "best_effort"      # wal_absent 视为通过，不是 unverified


def test_invalid_wal_yields_unverified_not_an_error(tmp_path):
    """identity 一致但 inspect_wal 判无效 → 仍返回结果，状态压到 unverified。"""
    src = make_source(tmp_path / "src")          # 默认写 GARBAGE_WAL
    res = sn.snapshot(src, tmp_path / "work", use_shm_lock=False)
    assert res.wal_path is not None
    assert res.validator_ok is True              # 主库本身是好的
    assert res.consistency == "unverified"


def test_valid_wal_yields_best_effort_without_lock(tmp_path):
    src = make_source(tmp_path / "src", wal_bytes=valid_wal_bytes())
    res = sn.snapshot(src, tmp_path / "work", use_shm_lock=False)
    assert res.consistency == "best_effort"
    assert res.locked is False


def test_wal_with_wrong_page_size_is_unverified(tmp_path):
    """page_size != 4096 的 WAL 不得算通过。"""
    src = make_source(tmp_path / "src")
    src.with_name(src.name + "-wal").write_bytes(
        fake.build_wal([(2, plain(2), 4)], aes_key=AES_KEY, mac_key=MAC_KEY,
                       mac="sha1", reserve=RESERVE, page_size=512))
    res = sn.snapshot(src, tmp_path / "work", use_shm_lock=False)
    assert res.consistency == "unverified"


def test_wal_page_size_checked_even_if_inspect_wal_claims_valid(tmp_path, monkeypatch):
    """§5.1 要求 valid 与 page_size==4096 **同时**成立。

    inspect_wal 自己也会因 page_size 不符而判 invalid，所以正常路径下这层是
    冗余的；这里直接伪造一个 valid=True 但 page_size=512 的 WalPlan，确保
    即使 inspect_wal 的守卫将来退化，本层仍然挡得住。
    """
    src = make_source(tmp_path / "src")
    bogus = sn.wal.WalPlan(True, "ok", 512, 1, 1, 4, b"", False)
    monkeypatch.setattr(sn.wal, "inspect_wal", lambda *a, **kw: bogus)
    res = sn.snapshot(src, tmp_path / "work", use_shm_lock=False)
    assert res.consistency == "unverified"


# --------------------------------------------------------- consistency 三态

def test_consistency_proven_when_lock_acquired(tmp_path, monkeypatch):
    src = make_source(tmp_path / "src", wal_bytes=valid_wal_bytes())
    monkeypatch.setattr(sn, "shm_lock", lambda p: const_cm(True))
    res = sn.snapshot(src, tmp_path / "work", use_shm_lock=True)
    assert res.locked is True and res.consistency == "proven"


def test_consistency_is_not_proven_without_lock(tmp_path, monkeypatch):
    src = make_source(tmp_path / "src", wal_bytes=valid_wal_bytes())
    monkeypatch.setattr(sn, "shm_lock", lambda p: const_cm(True))
    res = sn.snapshot(src, tmp_path / "work", use_shm_lock=False)
    assert res.locked is False and res.consistency == "best_effort"


def test_consistency_is_not_proven_when_identity_mismatched(tmp_path, monkeypatch):
    """拿到锁但 identity 不一致 → 这一轮作废，最终结果不得声称 proven。"""
    src = make_source(tmp_path / "src", wal_bytes=valid_wal_bytes())
    wal = src.with_name(src.name + "-wal")
    real = sn.copy_family
    monkeypatch.setattr(sn, "shm_lock", lambda p: const_cm(True))
    state = {"n": 0}

    def racy(src_db, dst_dir, **kw):
        out = real(src_db, dst_dir, **kw)
        state["n"] += 1
        if state["n"] == 1:
            with open(wal, "ab") as f:
                f.write(b"\x00" * 8)
        return out

    monkeypatch.setattr(sn, "copy_family", racy)
    res = sn.snapshot(src, tmp_path / "work", use_shm_lock=True)
    assert res.attempts == 2


def test_locked_reflects_the_successful_round_only(tmp_path, monkeypatch):
    """locked / consistency 必须反映**成功那一轮**。

    第 1 轮拿到了锁但 identity 不一致（副本作废），第 2 轮没拿到锁才成功——
    这时绝不能报告 locked=True / proven，因为返回的副本没有受锁保护。
    """
    src = make_source(tmp_path / "src", wal_bytes=valid_wal_bytes())
    wal = src.with_name(src.name + "-wal")
    real_copy = sn.copy_family
    state = {"locks": 0, "n": 0}

    def fake_lock(path):
        state["locks"] += 1
        return const_cm(state["locks"] == 1)      # 只有第 1 轮拿到锁

    def racy(src_db, dst_dir, **kw):
        state["n"] += 1
        out = real_copy(src_db, dst_dir, **kw)
        if state["n"] == 2:                       # 第 1 轮复制 db+wal 期间源变了
            with open(wal, "ab") as f:
                f.write(b"\x00" * 8)
        return out

    monkeypatch.setattr(sn, "shm_lock", fake_lock)
    monkeypatch.setattr(sn, "copy_family", racy)
    res = sn.snapshot(src, tmp_path / "work", use_shm_lock=True)

    assert res.attempts == 2
    assert res.locked is False, "第 2 轮没拿到锁，不得报告 locked=True"
    assert res.consistency == "best_effort"


@pytest.mark.win
@win_only
def test_consistency_proven_with_real_shm_lock(tmp_path):
    src = make_source(tmp_path / "src", shm_size=SHM_REAL_SIZE,
                      wal_bytes=valid_wal_bytes())
    res = sn.snapshot(src, tmp_path / "work", use_shm_lock=True)
    assert res.locked is True
    assert res.consistency == "proven"
    # 返回后锁必须已经释放，QQ 才能继续写
    assert range_is_free(src.with_name(src.name + "-shm"),
                         sn.SHM_LOCK_OFFSET, sn.SHM_LOCK_LENGTH)


@pytest.mark.win
@win_only
def test_shm_copy_is_byte_identical_even_under_lock(tmp_path):
    """持锁时 -shm 仍必须被完整复制（锁外那一步不能悄悄退化成不复制）。"""
    src = make_source(tmp_path / "src", shm_size=SHM_REAL_SIZE,
                      wal_bytes=valid_wal_bytes())
    src_shm = src.with_name(src.name + "-shm")
    # 用非零内容，否则「漏复制留下空文件」也可能看起来是对的
    src_shm.write_bytes(bytes((i * 7 + 13) % 256 for i in range(SHM_REAL_SIZE)))

    res = sn.snapshot(src, tmp_path / "work", use_shm_lock=True)

    assert res.consistency == "proven"
    assert res.shm_path is not None
    assert res.shm_path.read_bytes() == src_shm.read_bytes()


@pytest.mark.win
@win_only
def test_consistency_best_effort_when_lock_contended(tmp_path):
    """锁被别人（即 QQ）持有时：locked=False，consistency 只能是 best_effort。

    同一个场景还钉住另一条实测行为——QQ 写事务进行中锁着 120 字节，此时整个
    -shm 对我们不可读。它只是交叉校验用的可重建缓存，必须降级成「没有 -shm
    副本」，绝不能让整轮失败去重试（attempts 必须还是 1）。
    """
    src = make_source(tmp_path / "src", shm_size=SHM_REAL_SIZE,
                      wal_bytes=valid_wal_bytes())
    shm = src.with_name(src.name + "-shm")
    holder = open(shm, "r+b")
    try:
        holder.seek(sn.SHM_LOCK_OFFSET)
        msvcrt.locking(holder.fileno(), msvcrt.LK_NBLCK, sn.SHM_LOCK_LENGTH)
        res = sn.snapshot(src, tmp_path / "work", use_shm_lock=True)
    finally:
        holder.seek(sn.SHM_LOCK_OFFSET)
        msvcrt.locking(holder.fileno(), msvcrt.LK_UNLCK, sn.SHM_LOCK_LENGTH)
        holder.close()

    assert res.locked is False
    assert res.consistency == "best_effort"
    assert res.attempts == 1, "-shm 读不到不该触发重试"
    assert res.shm_path is None
    assert res.db_path.is_file() and res.wal_path is not None
    # build_wal 用随机 IV，不能与另一次构造的字节比较，只能与源文件比
    src_wal = src.with_name(src.name + "-wal")
    assert res.wal_path.read_bytes() == src_wal.read_bytes()
    assert res.db_path.read_bytes() == src.read_bytes()


# ------------------------------------------------------------ 源文件只读铁律

def test_snapshot_never_modifies_source_files(tmp_path):
    """spec §12 R9 的核心保障：源目录内容与 mtime 全部不变。"""
    src_root = tmp_path / "src"
    src = make_source(src_root)
    before_content = {p: p.read_bytes() for p in src_root.rglob("*") if p.is_file()}
    before_mtimes = mtimes(src_root)
    time.sleep(0.01)

    sn.snapshot(src, tmp_path / "work", use_shm_lock=False)

    assert mtimes(src_root) == before_mtimes
    assert {p: p.read_bytes() for p in src_root.rglob("*")
            if p.is_file()} == before_content


def test_source_identities_unchanged_after_successful_snapshot(tmp_path):
    """含 shm 锁的路径也只准读源：dev/ino/size/mtime_ns 与内容全部不变。"""
    src = make_source(tmp_path / "src", shm_size=SHM_REAL_SIZE,
                      wal_bytes=valid_wal_bytes())
    fam = [p for p in family(src) if p.exists()]
    before = [sn.file_identity(p) for p in fam]
    before_bytes = [p.read_bytes() for p in fam]

    res = sn.snapshot(src, tmp_path / "work", use_shm_lock=True)

    assert res.consistency in ("proven", "best_effort")
    assert [sn.file_identity(p) for p in fam] == before
    assert [p.read_bytes() for p in fam] == before_bytes


# ------------------------------------------------------------- validator 重试

def test_snapshot_retries_until_validator_passes(tmp_path):
    src = make_source(tmp_path / "src")
    calls = {"n": 0}

    def flaky(_path: Path) -> bool:
        calls["n"] += 1
        return calls["n"] >= 3

    res = sn.snapshot(src, tmp_path / "work", validator=flaky, use_shm_lock=False)
    assert res.attempts == 3 and res.validator_ok is True


def test_snapshot_raises_after_exhausting_attempts(tmp_path):
    src = make_source(tmp_path / "src")
    with pytest.raises(sn.SnapshotError) as ei:
        sn.snapshot(src, tmp_path / "work", validator=lambda p: False,
                    attempts=3, use_shm_lock=False)
    msg = str(ei.value)
    assert "退出 QQ" in msg and "3" in msg
    # 校验失败的路径也必须清干净，不得留下会被下次误用的副本
    assert list((tmp_path / "work").glob("nt_msg.db*")) == []


def test_snapshot_error_message_does_not_leak_paths_of_key_material(tmp_path):
    src = make_source(tmp_path / "src")
    with pytest.raises(sn.SnapshotError) as ei:
        sn.snapshot(src, tmp_path / "work", validator=lambda p: False,
                    attempts=2, use_shm_lock=False)
    assert PASSPHRASE.decode() not in str(ei.value)


def test_snapshot_reports_lock_outcome(tmp_path, monkeypatch):
    src = make_source(tmp_path / "src")
    monkeypatch.setattr(sn, "shm_lock", lambda p: const_cm(True))
    res = sn.snapshot(src, tmp_path / "work", use_shm_lock=True)
    assert res.locked is True and res.attempts == 1


def test_snapshot_skips_lock_when_disabled(tmp_path, monkeypatch):
    src = make_source(tmp_path / "src")
    called = {"n": 0}

    def spy(p):
        called["n"] += 1
        return const_cm(False)

    monkeypatch.setattr(sn, "shm_lock", spy)
    sn.snapshot(src, tmp_path / "work", use_shm_lock=False)
    assert called["n"] == 0


def test_result_records_bytes_copied(tmp_path):
    src = make_source(tmp_path / "src")
    expected = src.stat().st_size + len(GARBAGE_WAL) + 136
    res = sn.snapshot(src, tmp_path / "work", use_shm_lock=False)
    assert res.bytes_copied == expected


# ------------------------------------------------------- §5.2 措辞必须诚实

def test_wording_claims_best_effort_only(tmp_path):
    """docstring 与用户文案只能说「尽力获取一致快照」，不得声称已证明一致性。"""
    assert "尽力获取一致快照" in (sn.__doc__ or "")
    assert "尽力获取一致快照" in (sn.SnapshotResult.__doc__ or "")
    src = make_source(tmp_path / "src")
    with pytest.raises(sn.SnapshotError) as ei:
        sn.snapshot(src, tmp_path / "work", validator=lambda p: False,
                    attempts=1, use_shm_lock=False)
    msg = str(ei.value)
    assert "尽力获取一致快照" in msg
    assert "托盘" in msg
    for banned in ("已证明一致", "证明了一致", "一致性已证明", "proven"):
        assert banned not in msg


def test_result_field_order_is_stable():
    """兼容性硬约束：brief 的 7 个字段按序保留，consistency 追加在末尾。"""
    assert [f.name for f in dataclasses.fields(sn.SnapshotResult)] == [
        "db_path", "wal_path", "shm_path", "attempts", "locked",
        "validator_ok", "bytes_copied", "consistency"]


def test_constants_are_as_specified():
    assert (sn.SHM_LOCK_OFFSET, sn.SHM_LOCK_LENGTH) == (120, 3)
    assert sn.DEFAULT_ATTEMPTS == 3
    assert sn.COPY_CHUNK == 1024 * 1024


# ------------------------------------------------ shm 锁的机械语义（合成文件）

def test_shm_lock_yields_false_when_shm_absent(tmp_path):
    with sn.shm_lock(None) as locked:
        assert locked is False
    with sn.shm_lock(tmp_path / "missing-shm") as locked:
        assert locked is False


@pytest.mark.win
@win_only
def test_shm_lock_offset_and_length_on_synthetic_file(tmp_path):
    """在与真实 -shm 同大小（32768B）的合成文件上验证偏移 120、长度 3。

    下面的 119/120/123/124 刻意写成**字面量**而不是引用 sn.SHM_LOCK_*：
    否则常量被改错时本测试会跟着一起漂移，就失去判别力了。
    """
    shm = synthetic_shm(tmp_path)
    before = shm.read_bytes()
    with sn.shm_lock(shm) as locked:
        assert locked is True
        assert not range_is_free(shm, 120, 3), "[120,123) 应当被占住"
        assert range_is_free(shm, 119, 1), "偏移前移到了 119"
        assert range_is_free(shm, 123, 1), "长度越界到了 123（读锁区起点）"
        assert range_is_free(shm, 124, 1), "长度越界到了 124（实际读锁字节）"
    assert range_is_free(shm, 120, 3), "退出后没有释放"
    assert shm.read_bytes() == before, "取锁/放锁改动了 -shm 内容"


@pytest.mark.win
@win_only
def test_shm_lock_is_nonblocking_when_contended(tmp_path):
    """另一句柄已持锁时必须立刻失败，而不是 LK_LOCK 那样重试约 10 秒。"""
    shm = synthetic_shm(tmp_path)
    holder = open(shm, "r+b")
    try:
        holder.seek(sn.SHM_LOCK_OFFSET)
        msvcrt.locking(holder.fileno(), msvcrt.LK_NBLCK, sn.SHM_LOCK_LENGTH)
        t0 = time.monotonic()
        with sn.shm_lock(shm) as locked:
            assert locked is False
        elapsed = time.monotonic() - t0
        assert elapsed < 3.0, f"疑似阻塞重试而非非阻塞失败：{elapsed:.2f}s"
    finally:
        holder.seek(sn.SHM_LOCK_OFFSET)
        msvcrt.locking(holder.fileno(), msvcrt.LK_UNLCK, sn.SHM_LOCK_LENGTH)
        holder.close()
    assert range_is_free(shm, sn.SHM_LOCK_OFFSET, sn.SHM_LOCK_LENGTH)


@pytest.mark.win
@win_only
def test_shm_lock_degrades_when_file_too_short(tmp_path):
    """Windows 允许锁 EOF 之后的字节，所以必须显式挡下长度不足的文件。"""
    shm = tmp_path / "short-shm"
    shm.write_bytes(bytes(sn.SHM_LOCK_OFFSET + sn.SHM_LOCK_LENGTH - 1))   # 122 B
    with sn.shm_lock(shm) as locked:
        assert locked is False


@pytest.mark.win
@win_only
def test_shm_lock_degrades_when_open_fails(tmp_path, monkeypatch):
    """QQ 独占 -shm 导致打不开时优雅降级，绝不抛异常。"""
    shm = synthetic_shm(tmp_path)

    def deny(*a, **kw):
        raise OSError("simulated sharing violation")

    monkeypatch.setattr(builtins, "open", deny)
    with sn.shm_lock(shm) as locked:
        assert locked is False


@pytest.mark.win
@win_only
def test_shm_lock_degrades_when_locking_fails(tmp_path, monkeypatch):
    shm = synthetic_shm(tmp_path)

    def deny(*a, **kw):
        raise OSError("simulated lock failure")

    monkeypatch.setattr(msvcrt, "locking", deny)
    with sn.shm_lock(shm) as locked:
        assert locked is False


@pytest.mark.win
@win_only
def test_locked_shm_is_unreadable_from_another_handle(tmp_path):
    """特征化测试：钉住 Windows 的实测行为，它决定了 -shm 必须在锁外复制。

    字节范围锁按字节精确生效——不相交的区间另一个句柄照样能读；但任何**跨越**
    [120,123) 的读都会失败，而 Python 的缓冲读一次取 8192 字节必然跨越，
    于是持锁期间整个 -shm 对其它句柄不可读。只有持锁句柄自己能读完整文件。
    若哪天这条不再成立，把 -shm 挪回锁内复制才是更好的选择。
    """
    shm = synthetic_shm(tmp_path)
    holder = open(shm, "r+b")
    try:
        holder.seek(sn.SHM_LOCK_OFFSET)
        msvcrt.locking(holder.fileno(), msvcrt.LK_NBLCK, sn.SHM_LOCK_LENGTH)

        with pytest.raises(OSError):
            with open(shm, "rb") as f:
                f.read()                       # 缓冲读，跨越被锁字节

        fd = os.open(shm, os.O_RDONLY)
        try:
            assert len(os.read(fd, sn.SHM_LOCK_OFFSET)) == sn.SHM_LOCK_OFFSET
        finally:
            os.close(fd)                       # 不相交区间：不受影响

        holder.seek(0)
        assert len(holder.read()) == SHM_REAL_SIZE   # 持锁句柄自己能读全文
    finally:
        holder.seek(sn.SHM_LOCK_OFFSET)
        msvcrt.locking(holder.fileno(), msvcrt.LK_UNLCK, sn.SHM_LOCK_LENGTH)
        holder.close()


@pytest.mark.win
@win_only
def test_shm_lock_unlocks_and_closes_when_body_raises(tmp_path, monkeypatch):
    shm = synthetic_shm(tmp_path)
    handles: list = []
    real_open = builtins.open

    def spy(*a, **kw):
        fh = real_open(*a, **kw)
        handles.append(fh)
        return fh

    monkeypatch.setattr(builtins, "open", spy)
    with pytest.raises(RuntimeError, match="boom"):
        with sn.shm_lock(shm) as locked:
            assert locked is True
            raise RuntimeError("boom")
    monkeypatch.undo()

    mine = [h for h in handles
            if str(Path(getattr(h, "name", ""))) == str(shm)]
    assert mine, "从未打开过 -shm"
    assert all(h.closed for h in mine), "异常路径下句柄泄漏"
    assert range_is_free(shm, sn.SHM_LOCK_OFFSET, sn.SHM_LOCK_LENGTH), \
        "异常路径下没有解锁"
