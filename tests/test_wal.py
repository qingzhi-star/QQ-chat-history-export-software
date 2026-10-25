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
RESERVE = 48


def plain(tag: int) -> bytes:
    """正文全为 tag 的明网页，尾部 reserve（48 字节）区置零。

    与 decrypt_page 的输出形态一致（保留区零填充），round-trip 才字节相等；
    SQLCipher 的明网页保留区本就无语义，填 tag 是个陷阱（M5）。
    """
    return bytes([tag]) * (sc.PAGE_SZ - RESERVE) + b"\x00" * RESERVE


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
    """salt 不匹配 = 上一轮 checkpoint 遗留的已回收帧，有效序列到此为止。

    M1 判别性构造：陈旧帧的 salt 是错的，但其累计校验和按 **live 头的
    seed** 重算——校验和拦不住它，只有 salt 比较能拦。删掉 wal.py 里的
    salt 比较（`struct.unpack(">II", fh[8:16]) != hdr.salt`），陈旧帧会
    被计入，frame_count 变成 2，本测试必失败。
    """
    good = fake.build_wal([(2, plain(2), 4)], **KW)
    frame_sz = wal.WAL_FRAME_HEADER_SZ + sc.PAGE_SZ
    # live 头的 seed → 帧 1 之后的累计校验和
    s0, s1 = fake.wal_checksum(good[:24], 0, 0, False)
    s0, s1 = fake.wal_checksum(good[32:32 + 8] + good[32 + 24:32 + frame_sz],
                               s0, s1, False)
    # 陈旧帧：错误 salt，密文照常，校验和用 live seed 续算 → 结构上"有效"
    cipher = fake.encrypt_page(plain(3), 3, aes_key=AES_KEY, mac_key=MAC_KEY,
                               mac="sha1", reserve=RESERVE)
    stale_fh = struct.pack(">II", 3, 9) + struct.pack(">II", 0xAAAAAAAA, 0xBBBBBBBB)
    s0, s1 = fake.wal_checksum(stale_fh[:8] + cipher, s0, s1, False)
    blob = good + stale_fh + struct.pack(">II", s0, s1) + cipher

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


def test_inspect_falls_back_when_shm_mxframe_below_frame_count(tmp_path):
    """M3：约束是「mxFrame 小于**扫描所得帧数**」。此 WAL 扫描到 2 帧、
    最后 commit 是帧 1，mxFrame=1 满足旧的弱上界（>= last_commit）但不满足
    frame_count —— 必须降级。改回 `>= last_commit` 本测试必失败。"""
    blob = fake.build_wal([(2, plain(2), 4), (3, plain(3), 0)], **KW)
    w = write(tmp_path, "nt_msg.db-wal", blob)
    shm = write(tmp_path, "nt_msg.db-shm",
                fake.build_shm(page_size=sc.PAGE_SZ, mx_frame=1, n_page=4))
    plan = wal.inspect_wal(w, shm)
    assert plan.valid and plan.frame_count == 2 and plan.last_commit_frame == 1
    assert plan.shm_validated is False


def test_inspect_falls_back_when_shm_npage_disagrees(tmp_path):
    """M4：nPage 既不为 0 也不等于 commit_size → shm 与 WAL 不自洽，降级。
    删掉 nPage 交叉验证，本测试必失败（mxFrame 是对上的）。"""
    blob = fake.build_wal([(2, plain(2), 0), (3, plain(3), 5)], **KW)
    w = write(tmp_path, "nt_msg.db-wal", blob)
    shm = write(tmp_path, "nt_msg.db-shm",
                fake.build_shm(page_size=sc.PAGE_SZ, mx_frame=2, n_page=9))
    plan = wal.inspect_wal(w, shm)
    assert plan.valid and plan.commit_size == 5
    assert plan.shm_validated is False
    # nPage == 0（未知）不算矛盾，仍应通过交叉验证
    shm0 = write(tmp_path, "nt_msg0.db-shm",
                 fake.build_shm(page_size=sc.PAGE_SZ, mx_frame=2, n_page=0))
    w0 = write(tmp_path, "nt_msg0.db-wal", blob)
    assert wal.inspect_wal(w0, shm0).shm_validated is True


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


def test_apply_hmac_failure_before_commit_discards_uncommitted_frames(tmp_path):
    """R19：原子性边界是 commit 帧。帧 2 密文被篡改（帧校验和已重算，
    SQLite 层放行），页 HMAC 必然失败。帧 1 与帧 2 属于同一个**未提交**
    事务——它的页绝不允许写进数据库；整个事务被丢弃，库保持逐字节原样。

    这是「页 HMAC 失败出现在最后 commit 之前」的情形：截断长度为 0。
    与 test_apply_hmac_failure_after_last_commit... 成对，两者截断长度必须不同。
    （旧行为：帧 1 立即落盘 → applied=1、未提交的页 2 留在输出里——若
    回退到「边解密边写」的实现，db.read_bytes() == before 必失败。）
    """
    db = make_db(tmp_path, n_pages=4, filler=0xAA)
    before = db.read_bytes()
    blob = fake.build_wal([(2, plain(0x11), 0),      # 帧 1，未提交
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
    assert res.frames_applied == 0 and res.pages_written == 0
    assert res.stopped_early is True
    assert res.truncated_to_pages == 0
    assert db.read_bytes() == before        # 未提交事务一个字节都不许落地


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


def test_apply_skips_pgno_beyond_commit_size_and_continues(tmp_path):
    """R18：pgno > commit_size 不是损坏，而是合法形态——事务内先追加页、
    commit 前又释放（SQLite releasePage/bDoTruncate 会降低 commit 帧的
    nSize），walDecodeFrame 也不把高页号当损坏。只跳过该帧：不计入
    corrupt_frames、不置 stopped_early、notes 给出解释；后续有效帧照常
    应用，截断到最后的有效 commit，文件不被撑大。
    （旧行为：break + corrupt_frames=1 → 本测试的 corrupt==0/applied==1
    断言必失败。）"""
    db = make_db(tmp_path, n_pages=4, filler=0xAA)
    blob = fake.build_wal([(6, plain(0x11), 4),      # 帧 1：pgno 6 > commit_size 4 → 跳过
                           (2, plain(0x22), 4)],     # 帧 2：有效，照常应用
                          **KW)
    w = write(tmp_path, "nt_msg.db-wal", blob)
    plan = wal.inspect_wal(w)
    assert plan.valid and plan.commit_size == 4 and plan.last_commit_frame == 2

    params = sc.CipherParams("sha512", "sha1", 48, AES_KEY, MAC_KEY)
    res = wal.apply_wal(params, w, db, plan)
    assert res.corrupt_frames == 0                   # 不是损坏
    assert res.stopped_early is False                # 不中止回放
    assert res.frames_applied == 1 and res.pages_written == 1
    assert res.truncated_to_pages == 4               # 截断到最后的有效 commit
    assert any("skipped" in n for n in res.notes)    # notes 有解释行

    out = db.read_bytes()
    assert len(out) == 4 * sc.PAGE_SZ                # 文件没被 pgno 6 撑大
    assert out[sc.PAGE_SZ + 100] == 0x22             # 后续帧确实被应用


def test_apply_reviewer_scenario_shrinking_commit_keeps_contribution(tmp_path):
    """R18 评审原始场景：[pgno9 dbsize0, pgno10 dbsize10, pgno2 dbsize8]
    对 10 页库。旧行为 applied=0, corrupt=1, stopped=True, trunc=0——整个
    合法 WAL 的贡献被丢弃。新行为：帧 1/2 页号超出最终库大小（8 页）被
    跳过，帧 3 的真实贡献存活，截断到 8 页。"""
    db = make_db(tmp_path, n_pages=10, filler=0xAA)
    blob = fake.build_wal([(9, plain(0x99), 0),      # 帧 1：非 commit，pgno 9 > 8
                           (10, plain(0x10), 10),    # 帧 2：中间 commit，pgno 10 > 8
                           (2, plain(0x22), 8)],     # 帧 3：最后 commit，dbsize=8
                          **KW)
    w = write(tmp_path, "nt_msg.db-wal", blob)
    plan = wal.inspect_wal(w)
    assert plan.valid and plan.last_commit_frame == 3 and plan.commit_size == 8

    params = sc.CipherParams("sha512", "sha1", 48, AES_KEY, MAC_KEY)
    res = wal.apply_wal(params, w, db, plan)
    assert res.corrupt_frames == 0 and res.stopped_early is False
    assert res.frames_applied == 1 and res.pages_written == 1
    assert res.truncated_to_pages == 8
    assert sum("skipped" in n for n in res.notes) == 2

    out = db.read_bytes()
    assert len(out) == 8 * sc.PAGE_SZ                # 结果页数 = 最后 commit 的 8
    assert out[sc.PAGE_SZ + 100] == 0x22             # WAL 的真实贡献存活


def test_apply_hmac_failure_after_last_commit_truncates_to_earlier_commit(tmp_path):
    """与 test_apply_hmac_failure_before_commit_discards_uncommitted_frames 成对：
    帧 1 已 commit（dbsize=5），帧 2（同为 commit，dbsize=7）页 HMAC 失败
    → 截断到较早 commit 的 5 页。失败在 commit 之前时截断长度为 0——
    两种情形的截断长度必须不同。产物是一个有效但略陈旧的库，而不是一无所有。"""
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
    assert out[sc.PAGE_SZ + 100] == 0x11             # 较早 commit 的页保留
    assert out[4 * sc.PAGE_SZ: 4 * sc.PAGE_SZ + 4] == b"\x00" * 4   # 扩展区零填充


def test_apply_hmac_failure_mid_transaction_keeps_commit_drops_pending(tmp_path):
    """R19 核心场景：HMAC 失败发生在有效 commit **之后**、第二个事务中途。
    帧 2 可正常解密但属于未提交事务，帧 3 被篡改 → 第二个事务整体丢弃
    （帧 2 的页绝不能出现在输出里），截断到帧 1 commit 的 dbsize=4。
    判别性：旧实现边解密边写，帧 2 的页会留在输出（0x22），本测试的
    `out[2*PAGE_SZ+100] == 0xAA` 断言必失败。"""
    db = make_db(tmp_path, n_pages=4, filler=0xAA)
    blob = fake.build_wal([(2, plain(0x11), 4),      # 帧 1：commit，dbsize=4，已落盘
                           (3, plain(0x22), 0),      # 帧 2：第二事务，未提交，可解密
                           (4, plain(0x33), 0),      # 帧 3：将被篡改 → HMAC 失败
                           (5, plain(0x44), 6)],     # 帧 4：第二事务的 commit，不可达
                          **KW)
    blob = fake.tamper_page_and_resign(blob, 3)
    w = write(tmp_path, "nt_msg.db-wal", blob)

    plan = wal.inspect_wal(w)
    assert plan.valid and plan.last_commit_frame == 4 and plan.commit_size == 6

    params = sc.CipherParams("sha512", "sha1", 48, AES_KEY, MAC_KEY)
    res = wal.apply_wal(params, w, db, plan)
    assert res.corrupt_frames == 1 and res.stopped_early is True
    assert res.frames_applied == 1 and res.pages_written == 1
    assert res.truncated_to_pages == 4               # 较早 commit，而不是 6 或 0

    out = db.read_bytes()
    assert len(out) == 4 * sc.PAGE_SZ
    assert out[sc.PAGE_SZ + 100] == 0x11             # 已提交页保留 → 有效但略陈旧
    assert out[2 * sc.PAGE_SZ + 100] == 0xAA         # 未提交的帧 2 页绝不出现


def test_apply_degrades_to_reached_commit_when_structure_breaks_mid_wal(tmp_path):
    """R19 退化路径：inspect 之后帧 2 起的校验和被就地破坏（WAL 头未变，
    签名复查放行），pass 1 只能到帧 1。回放与截断都以**实际到达的**
    commit（帧 1，dbsize=4）为准，而不是 plan 里的 last_commit_frame=3 /
    commit_size=6。判别性：若回放上界/截断长度改回 plan 的原值，
    truncated_to_pages 会变成 6、文件被零填充撑大，本测试必失败。"""
    db = make_db(tmp_path, n_pages=4, filler=0xAA)
    blob = fake.build_wal([(2, plain(0x11), 4),      # 帧 1：commit，dbsize=4
                           (3, plain(0x22), 0),      # 帧 2：将被就地破坏校验和
                           (4, plain(0x33), 6)],     # 帧 3：plan 里的最后 commit
                          **KW)
    w = write(tmp_path, "nt_msg.db-wal", blob)
    plan = wal.inspect_wal(w)
    assert plan.valid and plan.last_commit_frame == 3 and plan.commit_size == 6

    raw = bytearray(w.read_bytes())
    frame_sz = wal.WAL_FRAME_HEADER_SZ + sc.PAGE_SZ
    raw[32 + frame_sz + 20] ^= 0xFF                  # 破坏帧 2 的校验和（头不动）
    w.write_bytes(bytes(raw))

    params = sc.CipherParams("sha512", "sha1", 48, AES_KEY, MAC_KEY)
    res = wal.apply_wal(params, w, db, plan)
    assert res.stopped_early is True
    assert res.frames_applied == 1 and res.truncated_to_pages == 4   # 不是 6

    out = db.read_bytes()
    assert len(out) == 4 * sc.PAGE_SZ                # 不按 plan 的 6 页撑大
    assert out[sc.PAGE_SZ + 100] == 0x11             # 实际到达的 commit 页保留
    assert out[2 * sc.PAGE_SZ + 100] == 0xAA         # 帧 2 及以后绝不写入


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
