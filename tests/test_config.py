from pathlib import Path

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


def test_load_tolerates_corrupt_json(tmp_path):
    p = tmp_path / "config.json"
    p.write_text("{not json", encoding="utf-8")
    assert config.Config.load(p) == config.Config()


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
