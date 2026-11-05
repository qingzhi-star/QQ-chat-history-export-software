from pathlib import Path

import json

from qqexport import __version__, gui
from qqexport.smoke import run_smoke
from tools import smoke_release


def test_smoke_constructs_main_window_without_qq_access(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("smoke must not access QQ")

    monkeypatch.setattr(gui.discover, "discover_accounts", forbidden)
    monkeypatch.setattr(gui.App, "_setup_logging", lambda self: None)
    path = tmp_path / "smoke.json"
    assert run_smoke(path) == 0
    result = json.loads(path.read_text(encoding="utf-8"))
    assert result["ok"] and result["version"] == __version__
    assert result["account_discovery"] is False


def test_smoke_guard_refuses_existing_test_install(tmp_path, monkeypatch):
    (tmp_path / "build-manifest.json").write_text('{"version":"0.1.0"}', encoding="utf-8")
    monkeypatch.setattr(smoke_release, "registry_exists", lambda: True)
    import pytest
    with pytest.raises(RuntimeError, match="拒绝覆盖"):
        smoke_release.smoke(tmp_path, Path("unused.exe"))
