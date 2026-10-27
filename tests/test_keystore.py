"""keystore 的单元测试。

鉴别力：损坏文件、未知版本、坏条目隔离的每条断言都对应实现里一段可删除的
校验——删掉它就会「静默抹掉其他账号的密钥」或「把坏条目当正常」。
DPAPI 的两个测试只在 Windows 上真跑；其余全部用假保护器，跨平台可测。
"""
import json
import threading
import time
from pathlib import Path

import pytest

from qqexport.core import keystore as ks

PASSPHRASE = b"#8FakeKeyForTest"
OTHER = b"#8AnotherKeyXXXX"


def fake_protect(data: bytes) -> bytes:
    return bytes(b ^ 0x5A for b in data)


def fake_unprotect(blob: bytes) -> bytes:
    return bytes(b ^ 0x5A for b in blob)


def store(tmp_path: Path, protect=fake_protect, unprotect=fake_unprotect,
          clock=lambda: "2026-10-24T00:00:00") -> ks.KeyStore:
    return ks.KeyStore(tmp_path / "keys.json", protect=protect,
                       unprotect=unprotect, clock=clock)


def write_doc(tmp_path: Path, doc) -> Path:
    path = tmp_path / "keys.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def doc_of(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "keys.json").read_text(encoding="utf-8"))


# ---------- fingerprint ----------

def test_fingerprint_is_eight_hex_chars():
    fp = ks.fingerprint(PASSPHRASE)
    assert len(fp) == ks.FINGERPRINT_LEN == 8
    assert all(c in "0123456789abcdef" for c in fp)


def test_fingerprint_is_stable_and_distinct():
    assert ks.fingerprint(PASSPHRASE) == ks.fingerprint(PASSPHRASE)
    assert ks.fingerprint(PASSPHRASE) != ks.fingerprint(OTHER)


def test_fingerprint_does_not_contain_passphrase():
    fp = ks.fingerprint(PASSPHRASE)
    assert PASSPHRASE.decode() not in fp
    assert PASSPHRASE.decode()[:4] not in fp


# ---------- DPAPI（仅 Windows） ----------

@pytest.mark.skipif(__import__("os").name != "nt", reason="DPAPI 仅 Windows")
def test_dpapi_roundtrip():
    blob = ks.dpapi_protect(PASSPHRASE)
    assert blob is not None and blob != PASSPHRASE
    assert ks.dpapi_unprotect(blob) == PASSPHRASE


@pytest.mark.skipif(__import__("os").name != "nt", reason="DPAPI 仅 Windows")
def test_dpapi_unprotect_rejects_garbage():
    assert ks.dpapi_unprotect(b"\x00" * 64) is None


# ---------- 基本存取 ----------

def test_save_then_load_roundtrip(tmp_path):
    s = store(tmp_path)
    s.save("123456789", PASSPHRASE)
    assert s.load("123456789") == PASSPHRASE


def test_load_unknown_uin_returns_none(tmp_path):
    assert store(tmp_path).load("99999999") is None


def test_load_from_fresh_instance_reads_disk(tmp_path):
    store(tmp_path).save("123456789", PASSPHRASE)
    assert store(tmp_path).load("123456789") == PASSPHRASE


def test_disk_never_contains_the_passphrase(tmp_path):
    s = store(tmp_path)
    s.save("123456789", PASSPHRASE)
    raw = (tmp_path / "keys.json").read_bytes()
    assert PASSPHRASE not in raw
    assert PASSPHRASE.decode() not in raw.decode("utf-8")
    assert doc_of(tmp_path)["keys"]["123456789"]["fingerprint"] == \
        ks.fingerprint(PASSPHRASE)


def test_save_records_fingerprint_and_timestamp(tmp_path):
    entry = store(tmp_path).save("123456789", PASSPHRASE)
    assert entry.uin == "123456789"
    assert entry.fingerprint == ks.fingerprint(PASSPHRASE)
    assert entry.saved_at == "2026-10-24T00:00:00"
    assert isinstance(entry.blob, str) and entry.blob
    assert entry.corrupt is False


def test_save_overwrites_same_uin(tmp_path):
    s = store(tmp_path)
    s.save("123456789", PASSPHRASE)
    s.save("123456789", OTHER)
    assert s.load("123456789") == OTHER
    assert len(s.entries()) == 1


def test_multiple_uins_are_independent(tmp_path):
    s = store(tmp_path)
    s.save("11111111", PASSPHRASE)
    s.save("22222222", OTHER)
    assert s.load("11111111") == PASSPHRASE
    assert s.load("22222222") == OTHER


def test_clear_single_uin_returns_count(tmp_path):
    s = store(tmp_path)
    s.save("11111111", PASSPHRASE)
    s.save("22222222", OTHER)
    assert s.clear("11111111") == 1
    assert s.load("11111111") is None
    assert s.load("22222222") == OTHER


def test_clear_all(tmp_path):
    s = store(tmp_path)
    s.save("11111111", PASSPHRASE)
    s.save("22222222", OTHER)
    assert s.clear() == 2
    assert s.entries() == []


def test_clear_unknown_uin_returns_zero(tmp_path):
    assert store(tmp_path).clear("99999999") == 0


def test_load_returns_none_when_blob_undecryptable(tmp_path):
    store(tmp_path).save("123456789", PASSPHRASE)
    s2 = ks.KeyStore(tmp_path / "keys.json", protect=fake_protect,
                     unprotect=lambda blob: None)
    assert s2.load("123456789") is None


def test_save_raises_when_protector_unavailable(tmp_path):
    s = store(tmp_path, protect=lambda data: None)
    with pytest.raises(ks.KeyStoreUnavailable) as ei:
        s.save("123456789", PASSPHRASE)
    assert PASSPHRASE.decode() not in str(ei.value)
    assert not (tmp_path / "keys.json").exists()


def test_save_leaves_no_tmp_file(tmp_path):
    store(tmp_path).save("123456789", PASSPHRASE)
    assert list(tmp_path.glob("*.tmp")) == []


def test_entries_expose_no_plaintext(tmp_path):
    s = store(tmp_path)
    s.save("123456789", PASSPHRASE)
    for e in s.entries():
        assert PASSPHRASE.decode() not in repr(e)


def test_redacted_dump_is_json_serialisable_and_clean(tmp_path):
    s = store(tmp_path)
    s.save("123456789", PASSPHRASE)
    dumped = s.redacted_dump()
    text = json.dumps(dumped, ensure_ascii=False)
    assert PASSPHRASE.decode() not in text
    assert dumped[0]["uin"] == "123456789"
    assert dumped[0]["fingerprint"] == ks.fingerprint(PASSPHRASE)
    assert "blob" not in dumped[0]


# ---------- 损坏的存储文件（修正案 A §7.1） ----------

def _corrupt_file(tmp_path: Path) -> Path:
    path = tmp_path / "keys.json"
    path.write_text("{not json", encoding="utf-8")
    return path


def test_corrupt_store_load_raises_with_recovery_guidance(tmp_path):
    _corrupt_file(tmp_path)
    with pytest.raises(ks.KeyStoreCorrupt) as ei:
        store(tmp_path).load("123456789")
    msg = str(ei.value)
    assert "keys.json" in msg
    assert ".bak" in msg
    assert "聊天记录" in msg
    assert "{not json" not in msg               # 不回显文件内容


def test_corrupt_store_save_never_overwrites(tmp_path):
    path = _corrupt_file(tmp_path)
    with pytest.raises(ks.KeyStoreCorrupt):
        store(tmp_path).save("123456789", PASSPHRASE)
    assert path.read_text(encoding="utf-8") == "{not json"


def test_corrupt_store_clear_raises(tmp_path):
    _corrupt_file(tmp_path)
    with pytest.raises(ks.KeyStoreCorrupt):
        store(tmp_path).clear()


def test_corrupt_store_entries_raises(tmp_path):
    _corrupt_file(tmp_path)
    with pytest.raises(ks.KeyStoreCorrupt):
        store(tmp_path).entries()


def test_store_file_with_unknown_version_is_corrupt(tmp_path):
    write_doc(tmp_path, {"version": 999, "keys": {}})
    with pytest.raises(ks.KeyStoreCorrupt):
        store(tmp_path).entries()


def test_store_file_without_version_is_corrupt(tmp_path):
    write_doc(tmp_path, {"keys": {}})
    with pytest.raises(ks.KeyStoreCorrupt):
        store(tmp_path).entries()


# ---------- 坏条目隔离（修正案 A §7.2） ----------

def test_bad_entry_is_isolated_and_marked_corrupt(tmp_path):
    s = store(tmp_path)
    s.save("11111111", PASSPHRASE)
    doc = doc_of(tmp_path)
    doc["keys"]["22222222"] = {"fingerprint": "zzzzzzzz", "blob": "!!!",
                               "saved_at": "not-a-date"}
    write_doc(tmp_path, doc)

    s2 = store(tmp_path)
    assert s2.load("11111111") == PASSPHRASE        # 好条目不受影响
    assert s2.load("22222222") is None              # 坏条目返回 None
    by_uin = {e.uin: e for e in s2.entries()}
    assert by_uin["11111111"].corrupt is False
    assert by_uin["22222222"].corrupt is True
    assert "!!!" not in repr(by_uin["22222222"])


@pytest.mark.parametrize("bad_uin,bad_raw", [
    ("22222222", {"fingerprint": "deadbeef", "blob": "@@@", "saved_at": "2026-10-24T00:00:00"}),
    ("22222222", {"fingerprint": "deadbee", "blob": "AAAA", "saved_at": "2026-10-24T00:00:00"}),
    ("22222222", {"fingerprint": "deadbeef", "blob": "AAAA", "saved_at": "24/10/2026"}),
    ("not-a-uin", {"fingerprint": "deadbeef", "blob": "AAAA", "saved_at": "2026-10-24T00:00:00"}),
    ("22222222", {"fingerprint": "deadbeef", "saved_at": "2026-10-24T00:00:00"}),
])
def test_invalid_entries_are_marked_corrupt(tmp_path, bad_uin, bad_raw):
    s = store(tmp_path)
    s.save("11111111", PASSPHRASE)
    doc = doc_of(tmp_path)
    doc["keys"][bad_uin] = bad_raw
    write_doc(tmp_path, doc)
    by_uin = {e.uin: e for e in store(tmp_path).entries()}
    assert by_uin[bad_uin].corrupt is True
    assert store(tmp_path).load(bad_uin) is None
    assert store(tmp_path).load("11111111") == PASSPHRASE


def test_write_failure_keeps_old_file_and_cleans_tmp(tmp_path, monkeypatch):
    """写盘途中失败：旧文件字节不变、无 .tmp 残留（修正案 A §0.6 / §7.3）。

    实现若直接写最终文件、或不在 finally 清理临时文件，这条失败。
    """
    s = store(tmp_path)
    s.save("11111111", PASSPHRASE)
    path = tmp_path / "keys.json"
    before = path.read_bytes()

    def boom(*args, **kwargs):
        raise OSError("模拟替换失败")

    monkeypatch.setattr(ks.os, "replace", boom)
    with pytest.raises(OSError):
        s.save("22222222", OTHER)
    assert path.read_bytes() == before
    assert list(tmp_path.glob("*.tmp")) == []


# ---------- 并发与原子写（修正案 A §7.3） ----------

def test_concurrent_saves_keep_both_entries(tmp_path, monkeypatch):
    """两个线程同时 save 不同 uin：最终两条都在，且无残留临时文件。

    测试用「写前固定延迟」把竞态窗口撑到确定：没有 read-modify-write 的锁时，
    两个线程都会先读到同一份旧内容，后写者覆盖先写者。
    """
    s = store(tmp_path)
    original_write = s._write_raw

    def slow_write(keys):
        time.sleep(0.02)                 # 两个线程都已读完、都还没写
        original_write(keys)

    monkeypatch.setattr(s, "_write_raw", slow_write)
    start = threading.Barrier(3, timeout=5)
    errors = []

    def worker(uin, key):
        start.wait()
        try:
            s.save(uin, key)
        except Exception as e:          # noqa: BLE001 - 测试要报告线程内异常
            errors.append(e)

    threads = [threading.Thread(target=worker, args=("11111111", PASSPHRASE)),
               threading.Thread(target=worker, args=("22222222", OTHER))]
    for t in threads:
        t.start()
    start.wait()                         # 主线程也等到齐，三个参与者
    for t in threads:
        t.join()

    assert errors == []
    assert {e.uin for e in s.entries()} == {"11111111", "22222222"}
    assert s.load("11111111") == PASSPHRASE
    assert s.load("22222222") == OTHER
    assert list(tmp_path.glob("*.tmp")) == []
