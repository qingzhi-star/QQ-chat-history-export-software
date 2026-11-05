"""固定构建输入、版本来源、可复现目录与 ZIP 的回归测试。"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import tomllib
import zipfile

import pytest

from qqexport import __version__
from tools import build_release as br


def test_runtime_version_comes_from_project():
    assert __version__ == br.project_version()
    br.sync_readme()


def test_readme_sync_follows_version_change(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "2.3.4"\n', encoding="utf-8")
    path = tmp_path / "README.md"
    path.write_text("before\n<!-- version:start -->\nold\n<!-- version:end -->\nafter", encoding="utf-8")
    with pytest.raises(ValueError, match="不同步"):
        br.sync_readme(tmp_path)
    br.sync_readme(tmp_path, write=True)
    assert "**2.3.4**" in path.read_text(encoding="utf-8")
    br.sync_readme(tmp_path)


def test_portable_zip_ignores_mtime_and_contains_only_bundle(tmp_path):
    bundle = tmp_path / "QQExport"
    (bundle / "_internal").mkdir(parents=True)
    exe = bundle / "QQExport.exe"
    exe.write_bytes(b"synthetic exe")
    (bundle / "_internal" / "runtime.txt").write_text("synthetic runtime", encoding="utf-8")
    one, two = tmp_path / "one.zip", tmp_path / "two.zip"
    br.write_portable(bundle, one, 315532800)
    os.utime(exe, (315532800, 315532800))
    br.write_portable(bundle, two, 315532800)
    assert br.sha256(one) == br.sha256(two)
    with zipfile.ZipFile(one) as zf:
        assert set(zf.namelist()) == {"QQExport/QQExport.exe", "QQExport/_internal/runtime.txt"}
        assert zf.read("QQExport/QQExport.exe") == b"synthetic exe"


def manifest(files):
    return {"version": "0.1.0", "python": "3.14.6", "requirements_sha256": "r",
            "toolchain_sha256": "t", "source_sha256": "s", "files": files}


def test_comparison_distinguishes_structure_and_bytes():
    one = manifest([{"path": "QQExport.exe", "size": 1, "sha256": "a"}])
    two = manifest([{"path": "QQExport.exe", "size": 1, "sha256": "b"}])
    assert br.compare_manifests(one, two) == {
        "structure_equal": True, "files": 1, "content_equal": False}
    two["files"].append({"path": "extra.txt", "size": 1, "sha256": "c"})
    with pytest.raises(ValueError, match="目录结构不一致"):
        br.compare_manifests(one, two)


def test_comparison_rejects_different_inputs():
    one, two = manifest([]), manifest([])
    two["source_sha256"] = "different"
    with pytest.raises(ValueError, match="不同构建输入"):
        br.compare_manifests(one, two)


def test_toolchain_rejects_modified_compiler(tmp_path, monkeypatch):
    for name in ("build-toolchain.json", ".python-version"):
        shutil.copyfile(br.ROOT / name, tmp_path / name)
    compiler = tmp_path / "ISCC.exe"
    compiler.write_bytes(b"synthetic modified compiler")
    expected = (tmp_path / ".python-version").read_text().strip()
    monkeypatch.setattr(br.platform, "python_version", lambda: expected)
    monkeypatch.setattr(br.platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(br.sys, "platform", "win32")
    with pytest.raises(ValueError, match="工具链哈希不符"):
        br.check_toolchain(tmp_path, compiler)


def test_build_rejects_unpinned_python(monkeypatch):
    monkeypatch.setattr(br.platform, "python_version", lambda: "0.0.0")
    with pytest.raises(ValueError, match="构建需要"):
        br.check_toolchain()


def test_build_refuses_existing_output_before_touching_it(tmp_path):
    marker = tmp_path / "keep.txt"
    marker.write_bytes(b"old build")
    with pytest.raises(ValueError, match="已存在"):
        br.build(tmp_path, iscc=None)
    assert marker.read_bytes() == b"old build"


def test_lock_is_complete_pinned_and_hashed():
    lines = [s for s in (br.ROOT / "requirements-build.lock").read_text().splitlines()
             if s and not s.startswith("#")]
    assert len(lines) >= 15
    assert all("==" in line and "--hash=sha256:" in line for line in lines)
    assert any(line.startswith("pyinstaller==") for line in lines)
    # 包元数据仍允许正常源码开发；发布时使用更严格的完整锁。
    project = tomllib.loads((br.ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "cryptography>=43" in project["project"]["dependencies"]


def test_manifest_has_no_absolute_paths(tmp_path):
    (tmp_path / "test.txt").write_text("synthetic", encoding="utf-8")
    entries = br.tree_manifest(tmp_path)
    assert entries[0]["path"] == "test.txt"
    assert str(tmp_path) not in json.dumps(entries)
