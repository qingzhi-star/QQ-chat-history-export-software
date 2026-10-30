"""T6：伴随库解密测试（合成加密库，不碰真实数据）。"""
from __future__ import annotations

from pathlib import Path

import pytest

from qqexport.core import companions
from tests.helpers import fake_sqlcipher as fake

PASS = b"#8FakeKeyForTest"
WRAPPER = b"QQ_NT DB" + b"\x00" * (1024 - 8)


def build_encrypted(tmp_path: Path) -> tuple[Path, bytes]:
    pages = fake.random_plain_pages(3)
    blob = fake.build_encrypted_db(pages, PASS, wrapper=WRAPPER)
    src = tmp_path / "profile_info.db"
    src.write_bytes(blob)
    return src, blob


def test_decrypt_companion_roundtrip_and_source_unchanged(tmp_path, monkeypatch):
    from qqexport.core import decrypt as dec
    monkeypatch.setattr(dec, "integrity_probe", lambda p: ("ok", []))
    src, blob = build_encrypted(tmp_path)
    out = companions.decrypt_companion(src, PASS, tmp_path / "work")
    assert out is not None and out.is_file()
    data = out.read_bytes()
    assert data[:16].startswith(b"SQLite format 3")
    assert len(data) == 3 * 4096
    assert src.read_bytes() == blob          # 源文件逐字节未变


def test_missing_companion_returns_none(tmp_path):
    assert companions.decrypt_companion(tmp_path / "nope.db", PASS,
                                        tmp_path / "work") is None


def test_wrong_passphrase_raises_companion_error(tmp_path):
    src, _ = build_encrypted(tmp_path)
    with pytest.raises(companions.CompanionError):
        companions.decrypt_companion(src, b"#8WrongKeyXXXXXX", tmp_path / "work")
