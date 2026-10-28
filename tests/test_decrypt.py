"""decrypt 的单元测试。

边界（计划 Task 8 明确写了，别再踩）：stock sqlite3 造不出「每页保留 48 字节」
的库，所以**不做**「解密合成库 → sqlite3 查询」的测试。职责切分：

- integrity_probe 单独对真实 sqlite3 明文库测试
- 解密页布局 / WAL 叠加与截断用随机页 round-trip 证明
- 真实产物可查询性只能由 Task 12 的 live 冒烟证明

合成明文库是随机页，quick_check 必然不 ok，因此需要「完整成功」语义的测试
统一用 `ok_integrity` 打桩；`failed` 状态另用真实 probe 单独测。
"""
import re
import sqlite3
import tracemalloc
from dataclasses import replace
from pathlib import Path

import pytest

from qqexport.core import decrypt as dec
from qqexport.core import sqlcipher as sc
from qqexport.core.discover import QQAccount
from tests.helpers import fake_sqlcipher as fake

PASSPHRASE = b"#8FakeKeyForTest"
WRONG = b"#8WrongKeyXXXXXX"


@pytest.fixture
def ok_integrity(monkeypatch):
    """把 integrity_probe 打桩成 ok：只用于验证页级产物与状态机。"""
    monkeypatch.setattr(dec, "integrity_probe",
                        lambda path: ("ok", ["c2c_msg_table"]))


def plain_page(tag: int) -> bytes:
    return bytes([tag]) * sc.PAGE_SZ


def build_source(tmp_path: Path, pages: list[bytes], *, name: str = "nt_msg.db",
                 wal_entries=None) -> tuple[Path, Path | None, Path | None]:
    db_dir = tmp_path / "123456789" / "nt_qq" / "nt_db"
    db_dir.mkdir(parents=True, exist_ok=True)
    db = db_dir / name
    db.write_bytes(fake.build_encrypted_db(pages, PASSPHRASE))
    w = s = None
    if wal_entries:
        salt = db.read_bytes()[sc.WRAPPER_SZ:sc.WRAPPER_SZ + sc.SALT_SZ]
        aes, mac = sc.derive_keys(PASSPHRASE, salt, "sha512")
        w = db_dir / (name + "-wal")
        w.write_bytes(fake.build_wal(wal_entries, aes_key=aes, mac_key=mac,
                                     mac="sha1", reserve=48, db_salt=salt))
        s = db_dir / (name + "-shm")
        s.write_bytes(b"\x00" * 136)
    return db, w, s


def page1_of(pages: list[bytes]) -> bytes:
    # 解密保留页 1 第 20 字节（SQLite reserved bytes）的原值；合成页本来就是 48。
    return bytes(pages[0])


def account_for(src_db: Path, uin: str = "123456789") -> QQAccount:
    return QQAccount(uin=uin, data_dir=src_db.parent.parent,
                     db_dir=src_db.parent,
                     media_dir=src_db.parent.parent / "nt_data")


# ---------- integrity_probe ----------

def test_integrity_probe_on_real_sqlite_db(tmp_path):
    p = tmp_path / "real.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE t (a INTEGER, b TEXT)")
    con.executemany("INSERT INTO t VALUES (?, ?)",
                    [(i, f"r{i}") for i in range(50)])
    con.commit()
    con.close()
    verdict, tables = dec.integrity_probe(p)
    assert verdict == "ok"
    assert "t" in tables


def test_integrity_probe_on_garbage(tmp_path):
    p = tmp_path / "garbage.db"
    p.write_bytes(b"\x00" * 8192)
    verdict, tables = dec.integrity_probe(p)
    assert verdict != "ok"
    assert tables == []


def test_integrity_probe_on_missing_file(tmp_path):
    verdict, tables = dec.integrity_probe(tmp_path / "nope.db")
    assert verdict.startswith("open_failed") and tables == []


def test_integrity_probe_opens_read_only(tmp_path):
    p = tmp_path / "real.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE t (a)")
    con.commit()
    con.close()
    p.chmod(0o444)
    try:
        verdict, _ = dec.integrity_probe(p)
        assert verdict == "ok"
    finally:
        p.chmod(0o666)
    assert list(tmp_path.glob("*-journal")) == []


def test_integrity_probe_handles_path_with_spaces(tmp_path):
    """本项目安装路径可能带空格；URI 必须能打开。"""
    d = tmp_path / "with space"
    d.mkdir()
    p = d / "real.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE t (a)")
    con.commit()
    con.close()
    assert dec.integrity_probe(p)[0] == "ok"


# ---------- decrypt_pages ----------

def test_decrypt_pages_roundtrip_is_byte_exact(tmp_path):
    pages = fake.random_plain_pages(6)
    src = tmp_path / "src.db"
    src.write_bytes(fake.build_encrypted_db(pages, PASSPHRASE))
    dst = tmp_path / "out.db"
    head = sc.read_header_and_page1(src)
    params = sc.detect_params(PASSPHRASE, head)
    n, corrupt, shifted = dec.decrypt_pages(src, dst, params)
    assert (n, corrupt, shifted) == (6, 0, False)
    out = dst.read_bytes()
    assert len(out) == 6 * sc.PAGE_SZ
    assert out[:sc.PAGE_SZ] == page1_of(pages[:1])
    for i in range(1, 6):
        assert out[i * sc.PAGE_SZ:(i + 1) * sc.PAGE_SZ] == pages[i]


def test_decrypt_pages_output_has_no_wrapper_header(tmp_path):
    pages = fake.random_plain_pages(2)
    src = tmp_path / "src.db"
    blob = fake.build_encrypted_db(pages, PASSPHRASE)
    src.write_bytes(blob)
    assert len(blob) == sc.WRAPPER_SZ + 2 * sc.PAGE_SZ
    dst = tmp_path / "out.db"
    params = sc.detect_params(PASSPHRASE, blob[:sc.WRAPPER_SZ + sc.PAGE_SZ])
    dec.decrypt_pages(src, dst, params)
    assert dst.stat().st_size == 2 * sc.PAGE_SZ          # 少了 1024 字节
    assert dst.read_bytes()[:16] == sc.SQLITE_MAGIC


def test_decrypt_pages_preserves_page1_reserved_bytes(tmp_path):
    """页 1 第 20 字节必须保留解密出的原值，不得改写成密文尾 reserve（R34）。

    真机实测 QQ 9.9.36 的 SQLite reserved bytes 是 80，而显式 reserve 是 48；
    改写成 48 会让 quick_check 对整个库报 Fragmentation。这里用 80 钉住保留语义。
    """
    pages = fake.random_plain_pages(1)
    assert pages[0][20] == 48
    weird = bytearray(pages[0])
    weird[20] = 80
    src = tmp_path / "src.db"
    src.write_bytes(fake.build_encrypted_db([bytes(weird)], PASSPHRASE))
    dst = tmp_path / "out.db"
    params = sc.detect_params(PASSPHRASE, sc.read_header_and_page1(src))
    dec.decrypt_pages(src, dst, params)
    assert dst.read_bytes()[20] == 80
    assert params.reserve == 48


def test_decrypt_pages_counts_corrupt_and_keeps_offsets(tmp_path):
    pages = fake.random_plain_pages(5)
    blob = bytearray(fake.build_encrypted_db(pages, PASSPHRASE))
    # 破坏第 3 页密文（页级 HMAC 必然失败），并保持文件长度不变
    off = sc.WRAPPER_SZ + 2 * sc.PAGE_SZ + 100
    blob[off] ^= 0xFF
    src = tmp_path / "src.db"
    src.write_bytes(bytes(blob))
    dst = tmp_path / "out.db"
    params = sc.detect_params(PASSPHRASE, bytes(blob[:sc.WRAPPER_SZ + sc.PAGE_SZ]))
    n, corrupt, _ = dec.decrypt_pages(src, dst, params)
    assert n == 5 and corrupt == 1
    out = dst.read_bytes()
    assert len(out) == 5 * sc.PAGE_SZ                    # 偏移未错位
    assert out[2 * sc.PAGE_SZ:3 * sc.PAGE_SZ] == b"\x00" * sc.PAGE_SZ
    assert out[3 * sc.PAGE_SZ:4 * sc.PAGE_SZ] == pages[3]  # 后续页仍正确


def test_decrypt_pages_passes_through_zero_lock_page(tmp_path):
    """用注入的 lock_page_no 覆盖该分支，无需真的造 1 GB 文件。"""
    pages = fake.random_plain_pages(3)
    blob = bytearray(fake.build_encrypted_db(pages, PASSPHRASE))
    off = sc.WRAPPER_SZ + 1 * sc.PAGE_SZ                 # 把第 2 页整体清零
    blob[off:off + sc.PAGE_SZ] = b"\x00" * sc.PAGE_SZ
    src = tmp_path / "src.db"
    src.write_bytes(bytes(blob))
    dst = tmp_path / "out.db"
    params = sc.detect_params(PASSPHRASE, bytes(blob[:sc.WRAPPER_SZ + sc.PAGE_SZ]))
    n, corrupt, shifted = dec.decrypt_pages(src, dst, params, lock_page_no=2)
    assert (n, corrupt, shifted) == (3, 0, True)
    assert dst.read_bytes()[sc.PAGE_SZ:2 * sc.PAGE_SZ] == b"\x00" * sc.PAGE_SZ


def test_decrypt_pages_zero_lock_page_at_real_number_is_untouched(tmp_path):
    """默认 lock_page_no=262144，小文件里的零页应被当作损坏页而非锁页。"""
    pages = fake.random_plain_pages(3)
    blob = bytearray(fake.build_encrypted_db(pages, PASSPHRASE))
    off = sc.WRAPPER_SZ + 1 * sc.PAGE_SZ
    blob[off:off + sc.PAGE_SZ] = b"\x00" * sc.PAGE_SZ
    src = tmp_path / "src.db"
    src.write_bytes(bytes(blob))
    dst = tmp_path / "out.db"
    params = sc.detect_params(PASSPHRASE, bytes(blob[:sc.WRAPPER_SZ + sc.PAGE_SZ]))
    _, corrupt, shifted = dec.decrypt_pages(src, dst, params)
    assert corrupt == 1 and shifted is False


def test_decrypt_pages_pads_truncated_tail(tmp_path):
    pages = fake.random_plain_pages(2)
    blob = fake.build_encrypted_db(pages, PASSPHRASE)
    src = tmp_path / "src.db"
    src.write_bytes(blob[:-1000])                        # 尾部被截断
    dst = tmp_path / "out.db"
    params = sc.detect_params(PASSPHRASE, blob[:sc.WRAPPER_SZ + sc.PAGE_SZ])
    n, corrupt, _ = dec.decrypt_pages(src, dst, params)
    assert n == 2 and corrupt == 1
    assert dst.stat().st_size == 2 * sc.PAGE_SZ


def test_decrypt_pages_memory_is_bounded(tmp_path):
    """200 页的库：峰值内存不得随库大小线性增长（修正案 A §8.4）。

    整库 read_bytes 的实现会在这里失败（200 页 = 800 KB，阈值 8 MB 仍可
    抓住「把整库载入内存再逐页写」的写法；阈值取 8 MB ≈ 2 页缓冲余量）。
    """
    pages = fake.random_plain_pages(200)
    src = tmp_path / "src.db"
    src.write_bytes(fake.build_encrypted_db(pages, PASSPHRASE))
    dst = tmp_path / "out.db"
    params = sc.detect_params(PASSPHRASE, sc.read_header_and_page1(src))
    tracemalloc.start()
    try:
        dec.decrypt_pages(src, dst, params)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 8 * 1024 * 1024
    assert dst.stat().st_size == 200 * sc.PAGE_SZ


# ---------- decrypt_file ----------

def test_decrypt_file_rejects_wrong_passphrase(tmp_path):
    src, _, _ = build_source(tmp_path, fake.random_plain_pages(2))
    with pytest.raises(dec.DecryptError) as ei:
        dec.decrypt_file(src, WRONG, tmp_path / "out.db")
    assert WRONG.decode() not in str(ei.value)           # 不泄露尝试过的口令


def test_decrypt_file_is_atomic_no_partial_left_on_failure(tmp_path):
    src, _, _ = build_source(tmp_path, fake.random_plain_pages(2))
    dst = tmp_path / "out.db"
    with pytest.raises(dec.DecryptError):
        dec.decrypt_file(src, WRONG, dst)
    assert not dst.exists()
    assert list(tmp_path.glob("*.partial")) == []


def test_decrypt_file_applies_wal_and_truncates(tmp_path, ok_integrity):
    """P0 验收核心：WAL 里的最新消息必须出现在产物中。"""
    pages = fake.random_plain_pages(4)
    newest = plain_page(0xEE)
    src, w, s = build_source(tmp_path, pages, wal_entries=[(3, newest, 6)])
    dst = tmp_path / "out.db"
    res = dec.decrypt_file(src, PASSPHRASE, dst, wal_path=w, shm_path=s)
    out = dst.read_bytes()
    assert len(out) == 6 * sc.PAGE_SZ                    # 截断到 commit_size
    assert out[2 * sc.PAGE_SZ: 2 * sc.PAGE_SZ + 4] == b"\xee" * 4
    assert res.wal_applied_frames == 1
    assert res.wal_frames == 1 and res.wal_last_commit_frame == 1
    assert res.wal_commit_size == 6 and res.wal_truncated is True
    assert res.status == "complete"


def test_decrypt_file_without_wal_leaves_main_db_alone(tmp_path, ok_integrity):
    pages = fake.random_plain_pages(4)
    src, _, _ = build_source(tmp_path, pages)
    dst = tmp_path / "out.db"
    res = dec.decrypt_file(src, PASSPHRASE, dst)
    assert dst.stat().st_size == 4 * sc.PAGE_SZ
    assert res.wal_frames == 0 and res.wal_reason == "wal_absent"
    assert res.wal_truncated is False
    assert res.status == "complete"


def test_decrypt_file_records_cipher_fingerprint(tmp_path, ok_integrity):
    src, _, _ = build_source(tmp_path, fake.random_plain_pages(1))
    res = dec.decrypt_file(src, PASSPHRASE, tmp_path / "out.db")
    assert res.cipher_fingerprint == "sha512/sha1/48"


def test_decrypt_file_collects_tables_and_integrity(tmp_path, ok_integrity):
    src, _, _ = build_source(tmp_path, fake.random_plain_pages(1))
    res = dec.decrypt_file(src, PASSPHRASE, tmp_path / "out.db")
    assert res.integrity_check == "ok"
    assert res.tables == ["c2c_msg_table"]
    assert res.duration_s >= 0.0


def test_decrypt_file_records_corrupt_page_warning(tmp_path, ok_integrity):
    pages = fake.random_plain_pages(3)
    blob = bytearray(fake.build_encrypted_db(pages, PASSPHRASE))
    blob[sc.WRAPPER_SZ + sc.PAGE_SZ + 50] ^= 0xFF
    src = tmp_path / "src" / "nt_msg.db"
    src.parent.mkdir()
    src.write_bytes(bytes(blob))
    res = dec.decrypt_file(src, PASSPHRASE, tmp_path / "out.db")
    assert res.corrupt_pages == 1
    assert any("corrupt" in w.lower() or "损坏" in w for w in res.warnings)


def test_status_lossy_when_corrupt_pages(tmp_path, ok_integrity):
    pages = fake.random_plain_pages(3)
    blob = bytearray(fake.build_encrypted_db(pages, PASSPHRASE))
    blob[sc.WRAPPER_SZ + sc.PAGE_SZ + 50] ^= 0xFF
    src = tmp_path / "src" / "nt_msg.db"
    src.parent.mkdir()
    src.write_bytes(bytes(blob))
    res = dec.decrypt_file(src, PASSPHRASE, tmp_path / "out.db")
    assert res.status == "lossy"


def test_status_lossy_when_wal_present_but_invalid(tmp_path, ok_integrity):
    src, _, _ = build_source(tmp_path, fake.random_plain_pages(2))
    bad_wal = src.parent / "nt_msg.db-wal"
    bad_wal.write_bytes(b"\x00" * 64)                    # 结构无效的 WAL
    res = dec.decrypt_file(src, PASSPHRASE, tmp_path / "out.db",
                           wal_path=bad_wal)
    assert res.status == "lossy"
    assert any("主库回退版" in w for w in res.warnings)


def test_status_failed_when_integrity_not_ok(tmp_path):
    """随机页库 quick_check 必然非 ok：failed 时删除产物、但返回结果对象。"""
    src, _, _ = build_source(tmp_path, fake.random_plain_pages(3))
    dst = tmp_path / "out.db"
    res = dec.decrypt_file(src, PASSPHRASE, dst)
    assert res.integrity_check != "ok"
    assert res.status == "failed"
    assert not dst.exists()
    assert list(tmp_path.glob("*.partial")) == []


def test_failed_keeps_old_product_intact(tmp_path):
    src, _, _ = build_source(tmp_path, fake.random_plain_pages(3))
    dst = tmp_path / "out.db"
    dst.write_bytes(b"OLD-PRODUCT")
    res = dec.decrypt_file(src, PASSPHRASE, dst)
    assert res.status == "failed"
    assert dst.read_bytes() == b"OLD-PRODUCT"


def test_status_lossy_when_snapshot_unverified(tmp_path, ok_integrity):
    src, _, _ = build_source(tmp_path, fake.random_plain_pages(2))
    res = dec.decrypt_file(src, PASSPHRASE, tmp_path / "out.db",
                           snapshot_consistency="unverified")
    assert res.status == "lossy"
    assert res.snapshot_consistency == "unverified"


def test_no_overwrite_refuses_existing_target(tmp_path, ok_integrity):
    src, _, _ = build_source(tmp_path, fake.random_plain_pages(2))
    dst = tmp_path / "out.db"
    dst.write_bytes(b"OLD")
    with pytest.raises(dec.DecryptError):
        dec.decrypt_file(src, PASSPHRASE, dst, overwrite=False)
    assert dst.read_bytes() == b"OLD"


def test_overwrite_replaces_existing_target(tmp_path, ok_integrity):
    src, _, _ = build_source(tmp_path, fake.random_plain_pages(2))
    dst = tmp_path / "out.db"
    dst.write_bytes(b"OLD")
    res = dec.decrypt_file(src, PASSPHRASE, dst)
    assert res.status == "complete"
    assert dst.read_bytes()[:16] == sc.SQLITE_MAGIC


def test_failure_injection_keeps_old_output_and_cleans_partial(tmp_path, monkeypatch):
    """与 Task 8 Step 4 的故障注入对应（修正案 A §0.6 / §8.2）。

    实现若直接写最终文件，或失败时不清理 partial，这条失败。
    """
    src, _, _ = build_source(tmp_path, fake.random_plain_pages(4))
    dst = tmp_path / "out.db"
    dst.write_bytes(b"OLD-PRODUCT")
    real = sc.decrypt_page
    calls = {"n": 0}

    def flaky(params, page, page_no):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("模拟页写入途中失败")
        return real(params, page, page_no)

    monkeypatch.setattr(dec.sc, "decrypt_page", flaky)
    with pytest.raises(RuntimeError):
        dec.decrypt_file(src, PASSPHRASE, dst)
    assert dst.read_bytes() == b"OLD-PRODUCT"
    assert list(tmp_path.rglob("*.partial")) == []


def test_partial_name_is_unique_per_process_and_random(tmp_path):
    dst = tmp_path / "out.db"
    a = dec._partial_path(dst)
    b = dec._partial_path(dst)
    assert a != b
    assert a.parent == dst.parent
    assert re.fullmatch(r"out\.db\.\d+\.[0-9a-f]{8}\.partial", a.name), a.name


# ---------- decrypt_database ----------

def test_decrypt_database_end_to_end_on_synthetic_account(tmp_path, ok_integrity):
    pages = fake.random_plain_pages(4)
    newest = plain_page(0xEE)
    src, w, s = build_source(tmp_path, pages, wal_entries=[(2, newest, 5)])
    account = account_for(src)
    dst_dir = tmp_path / "work"
    res = dec.decrypt_database(account, PASSPHRASE, dst_dir=dst_dir)

    assert res.plaintext_db.name == "nt_msg.decrypted.db"
    assert res.plaintext_db.is_file()
    assert res.plaintext_db.parent == dst_dir
    out = res.plaintext_db.read_bytes()
    assert len(out) == 5 * sc.PAGE_SZ
    assert out[sc.PAGE_SZ: sc.PAGE_SZ + 4] == b"\xee" * 4
    assert list(dst_dir.glob("*.partial")) == []
    assert res.status == "complete"


def test_decrypt_database_cleans_up_snapshot(tmp_path, ok_integrity):
    src, _, _ = build_source(tmp_path, fake.random_plain_pages(2))
    dst_dir = tmp_path / "work"
    dec.decrypt_database(account_for(src), PASSPHRASE, dst_dir=dst_dir)
    assert not (dst_dir / "nt_msg.db").exists()
    assert not (dst_dir / "nt_msg.db-wal").exists()
    assert not (dst_dir / "nt_msg.db-shm").exists()
    assert (dst_dir / "nt_msg.decrypted.db").is_file()


def test_decrypt_database_output_name_uses_plain_suffix(tmp_path, ok_integrity):
    assert dec.PLAIN_SUFFIX == ".decrypted.db"
    src, _, _ = build_source(tmp_path, fake.random_plain_pages(1),
                             name="profile_info.db")
    res = dec.decrypt_database(account_for(src), PASSPHRASE,
                               dst_dir=tmp_path / "work",
                               db_name="profile_info.db")
    assert res.plaintext_db.name == "profile_info.decrypted.db"


def test_decrypt_database_propagates_snapshot_error(tmp_path):
    src, _, _ = build_source(tmp_path, fake.random_plain_pages(1))
    src.write_bytes(b"\x00" * 4096)                       # 不再是合法 QQ 库
    with pytest.raises(dec.DecryptError) as ei:
        dec.decrypt_database(account_for(src), PASSPHRASE,
                             dst_dir=tmp_path / "work", attempts=2)
    assert "退出 QQ" in str(ei.value)


def test_decrypt_database_is_deterministic(tmp_path, ok_integrity):
    """spec §10.3 第 8 条：连续两次解密结果字节一致。"""
    pages = fake.random_plain_pages(3)
    src, _, _ = build_source(tmp_path, pages)
    account = account_for(src)
    a = dec.decrypt_database(account, PASSPHRASE, dst_dir=tmp_path / "w1")
    b = dec.decrypt_database(account, PASSPHRASE, dst_dir=tmp_path / "w2")
    assert a.plaintext_db.read_bytes() == b.plaintext_db.read_bytes()


def test_status_failed_when_page1_magic_missing(tmp_path, ok_integrity, monkeypatch):
    """页 1 在 detect 之后被替换：即使 quick_check 被打桩成 ok，也必须判 failed。

    真实可达路径是 TOCTOU：detect_params 用复制时的页 1 通过认证，随后解密读到的
    页 1 已不是原样（HMAC 失败 → 写零页 → 产物缺 SQLite 魔数）。这里先把原始头
    存下来给 detect，再把源文件的页 1 改坏，模拟这个窗口。
    """
    pages = fake.random_plain_pages(2)
    blob = bytearray(fake.build_encrypted_db(pages, PASSPHRASE))
    src = tmp_path / "123456789" / "nt_qq" / "nt_db" / "nt_msg.db"
    src.parent.mkdir(parents=True)
    src.write_bytes(bytes(blob))
    head = sc.read_header_and_page1(src)          # detect 用这份原始头
    blob[sc.WRAPPER_SZ + 100] ^= 0xFF             # 之后的页 1 认证失败
    src.write_bytes(bytes(blob))
    monkeypatch.setattr(dec.sc, "read_header_and_page1", lambda path: head)

    dst = tmp_path / "out.db"
    res = dec.decrypt_file(src, PASSPHRASE, dst)
    assert res.corrupt_pages >= 1
    assert res.status == "failed"
    assert not dst.exists()
    assert list(tmp_path.glob("*.partial")) == []


def test_decrypt_database_rejects_missing_db(tmp_path):
    data_dir = tmp_path / "x" / "nt_qq"
    (data_dir / "nt_db").mkdir(parents=True)
    (data_dir / "nt_data").mkdir(parents=True)
    account = QQAccount(uin="123456789", data_dir=data_dir,
                        db_dir=data_dir / "nt_db",
                        media_dir=data_dir / "nt_data")
    with pytest.raises(dec.DecryptError) as ei:
        dec.decrypt_database(account, PASSPHRASE, dst_dir=tmp_path / "work")
    assert "nt_msg.db" in str(ei.value)


def test_decrypt_database_unverified_snapshot_downgrades_status(tmp_path, monkeypatch):
    pages = fake.random_plain_pages(2)
    src, _, _ = build_source(tmp_path, pages)
    real = dec.snap.snapshot

    def wrapper(*args, **kwargs):
        return replace(real(*args, **kwargs), consistency="unverified")

    monkeypatch.setattr(dec.snap, "snapshot", wrapper)
    monkeypatch.setattr(dec, "integrity_probe",
                        lambda path: ("ok", ["c2c_msg_table"]))
    res = dec.decrypt_database(account_for(src), PASSPHRASE,
                               dst_dir=tmp_path / "work")
    assert res.snapshot_consistency == "unverified"
    assert res.status == "lossy"
