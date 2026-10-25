import os
import stat
import struct
from pathlib import Path

import pytest

from qqexport.core import sqlcipher as sc
from qqexport.core import wal
from tests.helpers import fake_sqlcipher as fake

PASSPHRASE = b"#8FakeKeyForTest"
DB_SALT = bytes(range(16))
AES_KEY, MAC_KEY = sc.derive_keys(PASSPHRASE, DB_SALT, "sha512")
KW = dict(aes_key=AES_KEY, mac_key=MAC_KEY, mac="sha1", reserve=48, db_salt=DB_SALT)


def plain(tag: int) -> bytes:
    return bytes([tag]) * sc.PAGE_SZ


def write(tmp_path: Path, name: str, data: bytes) -> Path:
    p = tmp_path / name
    p.write_bytes(data)
    return p


def make_db(tmp_path: Path, n_pages: int = 4, filler: int = 0) -> Path:
    p = tmp_path / "nt_msg.decrypted.db"
    p.write_bytes(bytes([filler]) * sc.PAGE_SZ * n_pages)
    return p


# ---------- parse_header ----------

def test_parse_header_accepts_valid_little_endian():
    blob = fake.build_wal([(2, plain(2), 0)], **KW)
    hdr = wal.parse_header(blob[:32])
    assert hdr is not None
    assert hdr.magic == wal.WAL_MAGIC_LE
    assert hdr.big_endian_checksum is False
    assert hdr.page_size == sc.PAGE_SZ
    assert hdr.salt == fake.DEFAULT_WAL_SALT
    assert hdr.seed == wal.checksum(blob[:24], 0, 0, False)


def test_parse_header_accepts_valid_big_endian():
    blob = fake.build_wal([(2, plain(2), 0)], big_endian=True, **KW)
    hdr = wal.parse_header(blob[:32])
    assert hdr is not None and hdr.big_endian_checksum is True


def test_parse_header_maps_page_size_one_to_65536():
    """只造 WAL 头、不造帧——编码值 1 表示 65536。"""
    blob = fake.build_wal([], page_size=1, **KW)
    hdr = wal.parse_header(blob[:32])
    assert hdr is not None and hdr.page_size == 65536


@pytest.mark.parametrize("mutate,offset", [
    (lambda b: b"\xff" + b[1:], 0),        # magic
    (lambda b: b[:4] + struct.pack(">I", 999) + b[8:], 4),   # version
    (lambda b: b[:24] + b"\xff" * 8, 24),  # header checksum
])
def test_parse_header_rejects_corrupt_headers(mutate, offset):
    blob = fake.build_wal([(2, plain(2), 0)], **KW)
    assert wal.parse_header(mutate(blob[:32])) is None


def test_parse_header_rejects_short_input():
    assert wal.parse_header(b"\x00" * 10) is None


def test_parse_header_rejects_non_power_of_two_page_size():
    blob = fake.build_wal([], page_size=4096, **KW)
    bad = blob[:8] + struct.pack(">I", 4000) + blob[12:]
    assert wal.parse_header(bad[:32]) is None


# ---------- checksum ----------

def test_checksum_rejects_unaligned_input():
    with pytest.raises(ValueError, match="8-byte"):
        wal.checksum(b"\x00" * 7, 0, 0, False)


def test_checksum_is_cumulative_and_order_sensitive():
    a = wal.checksum(b"\x01" * 8 + b"\x02" * 8, 0, 0, False)
    b = wal.checksum(b"\x02" * 8 + b"\x01" * 8, 0, 0, False)
    assert a != b
    step1 = wal.checksum(b"\x01" * 8, 0, 0, False)
    assert wal.checksum(b"\x02" * 8, *step1, False) == a


def test_checksum_endianness_matters():
    data = b"\x01\x02\x03\x04\x05\x06\x07\x08"
    assert wal.checksum(data, 0, 0, False) != wal.checksum(data, 0, 0, True)


# ---------- inspect_wal ----------

def test_inspect_counts_frames_and_finds_last_commit(tmp_path):
    blob = fake.build_wal([(2, plain(2), 0), (3, plain(3), 5),
                           (4, plain(4), 0), (5, plain(5), 7)], **KW)
    plan = wal.inspect_wal(write(tmp_path, "nt_msg.db-wal", blob))
    assert plan.valid and plan.reason == "ok"
    assert plan.frame_count == 4
    assert plan.last_commit_frame == 4
    assert plan.commit_size == 7
    assert plan.page_size == sc.PAGE_SZ
    assert plan.shm_validated is False


def test_inspect_reports_absent_wal(tmp_path):
    plan = wal.inspect_wal(tmp_path / "missing-wal")
    assert not plan.valid and plan.reason == "wal_absent"
    assert plan.frame_count == 0 and plan.last_commit_frame == 0


def test_inspect_rejects_page_size_mismatch(tmp_path):
    """只造头不造帧：页大小 1024 与期望的 4096 不符即应被判无效。"""
    blob = fake.build_wal([], page_size=1024, **KW)
    plan = wal.inspect_wal(write(tmp_path, "w", blob), expected_page_size=4096)
    assert not plan.valid and plan.reason == "page_size_mismatch"
    assert plan.page_size == 1024


def test_inspect_stops_at_bad_frame_checksum(tmp_path):
    blob = bytearray(fake.build_wal([(2, plain(2), 4), (3, plain(3), 5),
                                     (4, plain(4), 6)], **KW))
    frame_sz = wal.WAL_FRAME_HEADER_SZ + sc.PAGE_SZ
    first_commit = 32 + frame_sz          # 帧 1 结束后
    blob[first_commit + 20] ^= 0xFF       # 破坏帧 2 的校验和
    plan = wal.inspect_wal(write(tmp_path, "w", bytes(blob)))
    assert plan.valid
    assert plan.frame_count == 1
    assert plan.last_commit_frame == 1
    assert plan.commit_size == 4


def test_inspect_stops_at_recycled_frame_salt(tmp_path):
    """salt 不匹配 = 上一轮 checkpoint 遗留的已回收帧，有效序列到此为止。"""
    good = fake.build_wal([(2, plain(2), 4)], **KW)
    stale = fake.build_wal([(3, plain(3), 9)], salt=(0xAAAAAAAA, 0xBBBBBBBB), **KW)
    blob = good + stale[32:]
    plan = wal.inspect_wal(write(tmp_path, "w", blob))
    assert plan.frame_count == 1
    assert plan.last_commit_frame == 1 and plan.commit_size == 4


def test_inspect_ignores_truncated_tail_frame(tmp_path):
    blob = fake.build_wal([(2, plain(2), 4)], **KW)
    plan = wal.inspect_wal(write(tmp_path, "w", blob + b"\x00" * 100))
    assert plan.frame_count == 1 and plan.commit_size == 4


def test_inspect_handles_empty_wal(tmp_path):
    blob = fake.build_wal([], **KW)
    plan = wal.inspect_wal(write(tmp_path, "w", blob))
    assert plan.valid and plan.frame_count == 0
    assert plan.last_commit_frame == 0 and plan.commit_size == 0


def test_inspect_validates_consistent_shm(tmp_path):
    blob = fake.build_wal([(2, plain(2), 0), (3, plain(3), 5)], **KW)
    w = write(tmp_path, "nt_msg.db-wal", blob)
    shm = write(tmp_path, "nt_msg.db-shm",
                fake.build_shm(page_size=sc.PAGE_SZ, mx_frame=2, n_page=5))
    plan = wal.inspect_wal(w, shm)
    assert plan.valid and plan.shm_validated is True


def test_inspect_falls_back_when_shm_is_garbage(tmp_path):
    blob = fake.build_wal([(2, plain(2), 0), (3, plain(3), 5)], **KW)
    w = write(tmp_path, "nt_msg.db-wal", blob)
    shm = write(tmp_path, "nt_msg.db-shm", b"\x99" * 136)
    plan = wal.inspect_wal(w, shm)
    assert plan.valid and plan.shm_validated is False
    assert plan.last_commit_frame == 2 and plan.commit_size == 5


def test_inspect_falls_back_when_shm_copies_disagree(tmp_path):
    blob = fake.build_wal([(3, plain(3), 5)], **KW)
    good = fake.build_shm(page_size=sc.PAGE_SZ, mx_frame=1, n_page=5)
    w = write(tmp_path, "nt_msg.db-wal", blob)
    shm = write(tmp_path, "nt_msg.db-shm", good[:48] + b"\x00" * 48)
    assert wal.inspect_wal(w, shm).shm_validated is False


def test_inspect_falls_back_when_shm_salt_mismatches(tmp_path):
    blob = fake.build_wal([(3, plain(3), 5)], **KW)
    w = write(tmp_path, "nt_msg.db-wal", blob)
    shm = write(tmp_path, "nt_msg.db-shm",
                fake.build_shm(page_size=sc.PAGE_SZ, mx_frame=1, n_page=5,
                               salt=(0xDEAD, 0xBEEF)))
    assert wal.inspect_wal(w, shm).shm_validated is False


def test_inspect_falls_back_when_shm_mxframe_below_scan(tmp_path):
    """-shm 声称的 mxFrame 小于扫描所得 → 不可信，降级为仅扫描 WAL。"""
    blob = fake.build_wal([(2, plain(2), 0), (3, plain(3), 5)], **KW)
    w = write(tmp_path, "nt_msg.db-wal", blob)
    shm = write(tmp_path, "nt_msg.db-shm",
                fake.build_shm(page_size=sc.PAGE_SZ, mx_frame=1, n_page=5))
    plan = wal.inspect_wal(w, shm)
    assert plan.valid and plan.shm_validated is False
    assert plan.last_commit_frame == 2 and plan.commit_size == 5


# ---------- apply_wal ----------

def test_apply_writes_page_at_correct_offset_and_truncates(tmp_path):
    db = make_db(tmp_path, n_pages=4, filler=0xAA)
    blob = fake.build_wal([(3, plain(0x33), 6)], **KW)
    w = write(tmp_path, "nt_msg.db-wal", blob)
    plan = wal.inspect_wal(w)
    params = sc.CipherParams("sha512", "sha1", 48, AES_KEY, MAC_KEY)

    res = wal.apply_wal(params, w, db, plan)
    out = db.read_bytes()
    assert res.frames_applied == 1 and res.pages_written == 1
    assert res.corrupt_frames == 0 and not res.signature_changed
    assert len(out) == 6 * sc.PAGE_SZ                  # 截断到 commit_size
    assert out[2 * sc.PAGE_SZ: 2 * sc.PAGE_SZ + 4] == b"\x33" * 4   # 帧内页 3 已写入
    assert out[0:4] == b"\xaa" * 4                     # 未涉及的页保持原样
    # commit_size=6 大于主库的 4 页，truncate 向上扩展会零填充——这与 SQLite 语义一致
    assert out[4 * sc.PAGE_SZ: 4 * sc.PAGE_SZ + 4] == b"\x00" * 4


def test_apply_recovers_newest_version_when_page_written_twice(tmp_path):
    db = make_db(tmp_path, n_pages=4, filler=0xAA)
    blob = fake.build_wal([(2, plain(0x11), 0), (2, plain(0x22), 4)], **KW)
    w = write(tmp_path, "nt_msg.db-wal", blob)
    plan = wal.inspect_wal(w)
    params = sc.CipherParams("sha512", "sha1", 48, AES_KEY, MAC_KEY)
    wal.apply_wal(params, w, db, plan)
    out = db.read_bytes()
    page2 = out[sc.PAGE_SZ:2 * sc.PAGE_SZ]
    assert page2[100] == 0x22                      # 后写的帧覆盖先写的


def test_apply_stops_at_page_hmac_failure_keeping_earlier_frames(tmp_path):
    """帧 2 的密文被篡改（帧校验和已重算，故 SQLite 层放行），
    但 SQLCipher 页 HMAC 必然失败 → 停止应用后续帧，帧 1 的结果保留。

    这是「页 HMAC 失败出现在最后 commit 之前」的情形：截断长度为 0。
    与 test_apply_hmac_failure_after_last_commit... 成对，两者截断长度必须不同。
    """
    db = make_db(tmp_path, n_pages=4, filler=0xAA)
    blob = fake.build_wal([(2, plain(0x11), 0),      # 帧 1，非 commit
                           (3, plain(0x22), 0),      # 帧 2，将被篡改
                           (4, plain(0x33), 6)],     # 帧 3，commit
                          **KW)
    blob = fake.tamper_page_and_resign(blob, 2)
    w = write(tmp_path, "nt_msg.db-wal", blob)

    plan = wal.inspect_wal(w)
    assert plan.valid and plan.last_commit_frame == 3   # SQLite 层看不出问题

    params = sc.CipherParams("sha512", "sha1", 48, AES_KEY, MAC_KEY)
    res = wal.apply_wal(params, w, db, plan)
    assert res.corrupt_frames == 1
    assert res.frames_applied == 1 and res.pages_written == 1   # 只应用了帧 1
    assert res.stopped_early is True
    assert res.truncated_to_pages == 0                          # 未到 commit，不截断

    out = db.read_bytes()
    assert len(out) == 4 * sc.PAGE_SZ
    assert out[sc.PAGE_SZ + 100] == 0x11        # 帧 1（页 2）已写入
    assert out[2 * sc.PAGE_SZ + 100] == 0xAA    # 帧 2/3（页 3、4）未被写入


def test_inspect_does_not_detect_page_hmac_corruption(tmp_path):
    """明确记录职责边界：inspect_wal 只做 SQLite 层校验，页级 HMAC 由 apply_wal 负责。"""
    blob = fake.build_wal([(2, plain(0x11), 4)], **KW)
    blob = fake.tamper_page_and_resign(blob, 1)
    plan = wal.inspect_wal(write(tmp_path, "w", blob))
    assert plan.valid and plan.frame_count == 1 and plan.last_commit_frame == 1


def test_apply_is_noop_without_commit_frame(tmp_path):
    db = make_db(tmp_path, n_pages=4, filler=0xAA)
    before = db.read_bytes()
    blob = fake.build_wal([(2, plain(0x11), 0)], **KW)
    w = write(tmp_path, "nt_msg.db-wal", blob)
    plan = wal.inspect_wal(w)
    params = sc.CipherParams("sha512", "sha1", 48, AES_KEY, MAC_KEY)
    res = wal.apply_wal(params, w, db, plan)
    assert res.frames_applied == 0 and res.truncated_to_pages == 0
    assert db.read_bytes() == before


def test_apply_aborts_when_header_signature_changed(tmp_path):
    db = make_db(tmp_path, n_pages=4, filler=0xAA)
    before = db.read_bytes()
    blob = fake.build_wal([(2, plain(0x11), 4)], **KW)
    w = write(tmp_path, "nt_msg.db-wal", blob)
    plan = wal.inspect_wal(w)
    # inspect 之后 QQ 又写了新的 WAL（换了 salt）
    w.write_bytes(fake.build_wal([(2, plain(0x11), 4)],
                                 salt=(0x77777777, 0x88888888), **KW))
    params = sc.CipherParams("sha512", "sha1", 48, AES_KEY, MAC_KEY)
    res = wal.apply_wal(params, w, db, plan)
    assert res.signature_changed is True and res.frames_applied == 0
    assert db.read_bytes() == before


def test_apply_aborts_when_wal_rewritten_during_pass1(tmp_path, monkeypatch):
    """两遍之间必须复查 WAL 头签名：pass 1 扫描期间 QQ 重写了 WAL，
    则 pass 2 之前中止、绝不写入。删掉两遍之间的复查，本测试必失败
    （pass 2 会把新 WAL 的帧照常写进库）。"""
    db = make_db(tmp_path, n_pages=4, filler=0xAA)
    before = db.read_bytes()
    blob = fake.build_wal([(2, plain(0x11), 4)], **KW)
    w = write(tmp_path, "nt_msg.db-wal", blob)
    plan = wal.inspect_wal(w)

    real_iter = wal._iter_frames

    def iter_then_rewrite(f, hdr):
        frames = list(real_iter(f, hdr))
        # pass 1 扫描途中 QQ 重写 WAL（换 salt → 头签名变化，但页密文可解）
        w.write_bytes(fake.build_wal([(2, plain(0x11), 4)],
                                     salt=(0x77777777, 0x88888888), **KW))
        yield from frames

    monkeypatch.setattr(wal, "_iter_frames", iter_then_rewrite)

    params = sc.CipherParams("sha512", "sha1", 48, AES_KEY, MAC_KEY)
    res = wal.apply_wal(params, w, db, plan)
    assert res.signature_changed is True
    assert res.frames_applied == 0 and res.pages_written == 0
    assert db.read_bytes() == before


def test_apply_handles_page_one_frame(tmp_path):
    db = make_db(tmp_path, n_pages=4, filler=0xAA)
    p1 = sc.SQLITE_MAGIC + b"\x00" * 4 + bytes([48]) + b"\x5a" * (sc.PAGE_SZ - 21)
    blob = fake.build_wal([(1, p1, 4)], **KW)
    w = write(tmp_path, "nt_msg.db-wal", blob)
    plan = wal.inspect_wal(w)
    params = sc.CipherParams("sha512", "sha1", 48, AES_KEY, MAC_KEY)
    wal.apply_wal(params, w, db, plan)
    out = db.read_bytes()
    assert out[:16] == sc.SQLITE_MAGIC
    assert out[20] == 48


# ---------- 修正案 A §4.1：commit 语义显式状态机 ----------

def test_apply_multiple_commits_truncates_to_last_commit(tmp_path):
    """多个 commit 帧：只回放到最后一个有效 commit，
    中间 commit 的 dbsize（5）不得成为最终截断长度（应为 8）。"""
    db = make_db(tmp_path, n_pages=4, filler=0xAA)
    blob = fake.build_wal([(2, plain(0x11), 0),      # 帧 1：非 commit
                           (3, plain(0x22), 5),      # 帧 2：中间 commit，dbsize=5
                           (4, plain(0x33), 8)],     # 帧 3：最后 commit，dbsize=8
                          **KW)
    w = write(tmp_path, "nt_msg.db-wal", blob)
    plan = wal.inspect_wal(w)
    assert plan.valid and plan.last_commit_frame == 3 and plan.commit_size == 8

    params = sc.CipherParams("sha512", "sha1", 48, AES_KEY, MAC_KEY)
    res = wal.apply_wal(params, w, db, plan)
    out = db.read_bytes()
    assert res.truncated_to_pages == 8               # 不是中间 commit 的 5
    assert len(out) == 8 * sc.PAGE_SZ
    assert out[2 * sc.PAGE_SZ + 100] == 0x22         # 中间 commit 的页仍被写入
    assert out[3 * sc.PAGE_SZ + 100] == 0x33         # 最后 commit 的页也被写入


def test_apply_never_writes_uncommitted_frames_after_commit(tmp_path):
    """commit 帧之后的未提交帧绝不写入。"""
    db = make_db(tmp_path, n_pages=4, filler=0xAA)
    blob = fake.build_wal([(2, plain(0x11), 4),      # 帧 1：commit，dbsize=4
                           (3, plain(0x22), 0)],     # 帧 2：未提交，不得写入
                          **KW)
    w = write(tmp_path, "nt_msg.db-wal", blob)
    plan = wal.inspect_wal(w)
    assert plan.last_commit_frame == 1

    params = sc.CipherParams("sha512", "sha1", 48, AES_KEY, MAC_KEY)
    res = wal.apply_wal(params, w, db, plan)
    out = db.read_bytes()
    assert res.frames_applied == 1 and res.truncated_to_pages == 4
    assert out[sc.PAGE_SZ + 100] == 0x11             # 帧 1 已写入
    assert out[2 * sc.PAGE_SZ + 100] == 0xAA         # 帧 2（页 3）未写入，保持填充


def test_apply_rejects_pgno_zero(tmp_path):
    """pgno == 0 会让 db.seek((pgno-1)*page_size) 落到负偏移（Windows 上抛
    OSError）。必须由显式守卫拦下并计入 corrupt_frames，而不是冒出 I/O 错误。"""
    db = make_db(tmp_path, n_pages=4, filler=0xAA)
    before = db.read_bytes()
    blob = fake.build_wal([(0, plain(0x11), 4)], **KW)
    w = write(tmp_path, "nt_msg.db-wal", blob)
    plan = wal.inspect_wal(w)
    assert plan.valid and plan.last_commit_frame == 1   # SQLite 层不检查页号

    params = sc.CipherParams("sha512", "sha1", 48, AES_KEY, MAC_KEY)
    res = wal.apply_wal(params, w, db, plan)            # 不得抛异常
    assert res.corrupt_frames == 1 and res.frames_applied == 0
    assert res.stopped_early is True and res.truncated_to_pages == 0
    assert db.read_bytes() == before


def test_apply_rejects_pgno_beyond_commit_size(tmp_path):
    """pgno > commit_size：拒绝写入、计入 corrupt_frames，不得把文件撑大。"""
    db = make_db(tmp_path, n_pages=4, filler=0xAA)
    before = db.read_bytes()
    blob = fake.build_wal([(6, plain(0x11), 4)], **KW)   # pgno 6 > commit_size 4
    w = write(tmp_path, "nt_msg.db-wal", blob)
    plan = wal.inspect_wal(w)
    assert plan.valid and plan.commit_size == 4

    params = sc.CipherParams("sha512", "sha1", 48, AES_KEY, MAC_KEY)
    res = wal.apply_wal(params, w, db, plan)
    assert res.corrupt_frames == 1 and res.frames_applied == 0
    assert res.stopped_early is True and res.truncated_to_pages == 0
    assert db.read_bytes() == before


def test_apply_hmac_failure_after_last_commit_truncates_to_earlier_commit(tmp_path):
    """与 test_apply_stops_at_page_hmac_failure_keeping_earlier_frames 成对：
    帧 1 已 commit（dbsize=5），帧 2（同为 commit，dbsize=7）页 HMAC 失败
    → 截断到较早 commit 的 5 页。失败在 commit 之前时截断长度为 0——
    两种情形的截断长度必须不同。"""
    db = make_db(tmp_path, n_pages=4, filler=0xAA)
    blob = fake.build_wal([(2, plain(0x11), 5),      # 帧 1：commit，dbsize=5
                           (3, plain(0x22), 7)],     # 帧 2：commit，dbsize=7，将被篡改
                          **KW)
    blob = fake.tamper_page_and_resign(blob, 2)      # 帧校验和重签，只坏页 HMAC
    w = write(tmp_path, "nt_msg.db-wal", blob)

    plan = wal.inspect_wal(w)
    assert plan.valid and plan.last_commit_frame == 2 and plan.commit_size == 7

    params = sc.CipherParams("sha512", "sha1", 48, AES_KEY, MAC_KEY)
    res = wal.apply_wal(params, w, db, plan)
    assert res.corrupt_frames == 1 and res.stopped_early is True
    assert res.frames_applied == 1 and res.pages_written == 1
    assert res.truncated_to_pages == 5               # ≠ 失败在 commit 之前时的 0

    out = db.read_bytes()
    assert len(out) == 5 * sc.PAGE_SZ
    assert out[sc.PAGE_SZ + 100] == 0x11             # 帧 1（页 2）已写入
    assert out[4 * sc.PAGE_SZ: 4 * sc.PAGE_SZ + 4] == b"\x00" * 4   # 扩展区零填充


# ---------- 修正案 A §4.2：回放只写可写的解密产物 ----------

def test_apply_raises_oserror_when_db_not_writable(tmp_path):
    """目标库不可写时必须抛 OSError，而不是静默成功。

    Windows 上给目录设只读属性并不能阻止在其中写文件（实测放行），
    给文件本身设只读才是本机等价且可靠的构造：open(.., 'r+b') 抛
    PermissionError ⊂ OSError。"""
    db = make_db(tmp_path, n_pages=4, filler=0xAA)
    blob = fake.build_wal([(2, plain(0x11), 4)], **KW)
    w = write(tmp_path, "nt_msg.db-wal", blob)
    plan = wal.inspect_wal(w)
    params = sc.CipherParams("sha512", "sha1", 48, AES_KEY, MAC_KEY)
    os.chmod(db, stat.S_IREAD)
    try:
        with pytest.raises(OSError):
            wal.apply_wal(params, w, db, plan)
    finally:
        os.chmod(db, stat.S_IWRITE | stat.S_IREAD)


# ---------- 校验和手算向量 ----------

def test_checksum_matches_hand_computed_vectors():
    """算法：s0 = s0+v0+s1; s1 = s1+v1+s0（用更新后的 s0），每步 mod 2^32。

    (v0,v1)=(1,2), seed(0,0) → s0 = 0+1+0 = 1, s1 = 0+2+1 = 3
    """
    assert wal.checksum(b"\x00" * 8, 0, 0, False) == (0, 0)
    assert wal.checksum(struct.pack("<II", 1, 2), 0, 0, False) == (1, 3)
    # 再喂一块 (3,4)：s0 = 1+3+3 = 7, s1 = 3+4+7 = 14
    assert wal.checksum(struct.pack("<II", 3, 4), 1, 3, False) == (7, 14)
    # 大端读同一批字节得到不同的 v0/v1
    assert wal.checksum(struct.pack(">II", 1, 2), 0, 0, True) == (1, 3)
    assert wal.checksum(struct.pack("<II", 1, 2), 0, 0, True) != (1, 3)


def test_checksum_wraps_at_32_bits():
    s0, s1 = wal.checksum(struct.pack("<II", 0xFFFFFFFF, 0xFFFFFFFF), 0, 0, False)
    assert 0 <= s0 <= 0xFFFFFFFF and 0 <= s1 <= 0xFFFFFFFF
    assert s0 == 0xFFFFFFFF
