from pathlib import Path

import pytest

from qqexport import __version__
from qqexport import config


def test_version_is_string():
    assert isinstance(__version__, str) and __version__


def test_app_dir_honours_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("QQEXPORT_HOME", str(tmp_path / "home"))
    assert config.app_dir() == tmp_path / "home"


def test_app_dir_uses_localappdata(monkeypatch, tmp_path):
    monkeypatch.delenv("QQEXPORT_HOME", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "lad"))
    assert config.app_dir() == tmp_path / "lad" / "QQExport"


def test_derived_paths_live_under_app_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("QQEXPORT_HOME", str(tmp_path))
    assert config.config_path() == tmp_path / "config.json"
    assert config.keys_path() == tmp_path / "keys.json"
    assert config.work_dir("12345") == tmp_path / "work" / "12345"


def test_load_returns_defaults_when_file_missing(tmp_path):
    assert config.Config.load(tmp_path / "nope.json") == config.Config()


def test_roundtrip_preserves_all_fields(tmp_path):
    p = tmp_path / "config.json"
    original = config.Config(data_dir="D:/x", uin="123456789",
                             cache_key=False, decode_voice=True)
    original.save(p)
    assert config.Config.load(p) == original


def test_load_ignores_keys_from_newer_version(tmp_path):
    p = tmp_path / "config.json"
    p.write_text('{"uin": "1", "from_the_future": 9}', encoding="utf-8")
    assert config.Config.load(p).uin == "1"


@pytest.mark.parametrize("raw", [
    b"{not json", b"[]", b"null", b"\xff", b'{"uin": 12345}',
    b'{"cache_key": "false"}', b'{"decode_voice": 1}', b'{"data_dir": []}',
])
def test_corrupt_config_is_reported_and_cannot_be_overwritten(tmp_path, raw):
    p = tmp_path / "config.json"
    p.write_bytes(raw)
    with pytest.raises(config.ConfigError, match="config.json"):
        config.Config.load(p)
    with pytest.raises(config.ConfigError, match="config.json"):
        config.Config().save(p)
    assert p.read_bytes() == raw
    assert list(tmp_path.glob("*.tmp")) == []


def test_save_creates_parent_dirs_and_leaves_no_tmp(tmp_path):
    p = tmp_path / "nested" / "deeper" / "config.json"
    config.Config(uin="1").save(p)
    assert p.is_file()
    assert list(p.parent.glob("*.tmp")) == []


def test_save_overwrites_existing(tmp_path):
    p = tmp_path / "config.json"
    config.Config(uin="old").save(p)
    config.Config(uin="new").save(p)
    assert config.Config.load(p).uin == "new"


def test_old_partial_config_keeps_defaults(tmp_path):
    p = tmp_path / "config.json"
    p.write_text('{"uin": "12345"}', encoding="utf-8")
    assert config.Config.load(p) == config.Config(uin="12345")


@pytest.mark.parametrize("step", ["write", "replace"])
def test_atomic_save_failure_keeps_original_and_cleans_tmp(tmp_path, monkeypatch, step):
    p = tmp_path / "config.json"
    config.Config(uin="12345").save(p)
    old = p.read_bytes()

    def fail(*args, **kwargs):
        if step == "write":
            args[1].write("partial")
        raise OSError("synthetic-private-path")

    if step == "write":
        monkeypatch.setattr(config.json, "dump", fail)
    else:
        monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(config.ConfigError) as exc:
        config.Config(uin="987654321").save(p)
    assert "synthetic-private-path" not in str(exc.value)
    assert p.read_bytes() == old
    assert list(tmp_path.glob("*.tmp")) == []


def test_unreadable_config_is_not_treated_as_missing(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise PermissionError("synthetic-private-path")

    monkeypatch.setattr(Path, "read_text", fail)
    with pytest.raises(config.ConfigError, match="无法读取"):
        config.Config.load(tmp_path / "config.json")
