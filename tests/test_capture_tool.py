"""capture_fixtures.py 的纯函数测试（不需真机、不需 fixture 文件）。

钉住修正案 A §11.2 的「全部可打印串、不截断」与 §11.3 的「provenance 最小化」。
"""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location(
    "capture_fixtures", ROOT / "tools" / "capture_fixtures.py")
cf = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(cf)


def test_printable_strings_splits_on_nul_and_non_printable():
    data = b"\x00\x01sqlite_master\x00c2c_msg_table\x00\xff\x10group_msg_table"
    assert cf.printable_strings(data) == [
        "sqlite_master", "c2c_msg_table", "group_msg_table"]


def test_printable_strings_does_not_truncate_long_runs():
    long_text = "CREATE TABLE " + "a" * 500
    assert cf.printable_strings(long_text.encode("ascii") + b"\x00") == [long_text]


def test_printable_strings_keeps_order_and_short_fragments_filtered():
    data = b"\x00abc\x00abcd\x0040001\x00"
    assert cf.printable_strings(data) == ["abcd", "40001"]


def test_confirm_phrase_is_the_reviewed_one():
    assert cf.CONFIRM_PHRASE == "I HAVE REVIEWED THE STRINGS"


def test_build_provenance_is_minimal_and_date_only():
    doc = cf.build_provenance(
        captured_at="2026-10-28", qq_version="9.9.36-53644",
        source_page_count=90160, captured_bytes=5120,
        cipher_fingerprint="sha512/sha1/48")
    assert doc == {
        "captured_at": "2026-10-28",
        "qq_version": "9.9.36-53644",
        "source_page_count": 90160,
        "has_wrapper_header": True,
        "captured_bytes": 5120,
        "cipher_fingerprint": "sha512/sha1/48",
    }
    # 明确禁止的字段一个都不能出现
    for forbidden in ("uin", "path", "key_fingerprint", "source_size_bytes",
                      "passphrase"):
        assert forbidden not in doc
