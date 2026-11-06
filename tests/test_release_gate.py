import json
import pytest
from tools import release_gate as gate, build_release as br


def make_release(tmp_path, version="0.1.1rc1"):
    bundle = tmp_path / "QQExport"
    bundle.mkdir()
    (bundle / "QQExport.exe").write_bytes(b"synthetic executable")
    artifacts = []
    for name in (f"QQExport-portable-{version}.zip", f"QQExport-Setup-{version}.exe"):
        path = tmp_path / name
        path.write_bytes(b"synthetic asset")
        artifacts.append({"path": name, "size": path.stat().st_size, "sha256": br.sha256(path)})
    manifest = {"version": version, "source_commit": "synthetic", "source_dirty": False,
                "release_title": f"qqexport v{version}", "artifacts": artifacts,
                "files": br.tree_manifest(bundle)}
    path = tmp_path / "build-manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    (tmp_path / "SHA256SUMS").write_text("".join(
        f"{a['sha256']}  {a['path']}\n" for a in artifacts), encoding="ascii")
    smoke = {"ok": True, "version": version, "build_manifest_sha256": br.sha256(path),
             "checks": dict.fromkeys(gate.SMOKE_CHECKS, True)}
    return smoke


def test_candidate_accepts_bound_evidence(tmp_path):
    smoke = make_release(tmp_path)
    assert gate.validate(tmp_path, smoke, "v0.1.1rc1", "0.1.1rc1", "synthetic") == []


def test_stale_smoke_or_changed_asset_is_rejected(tmp_path):
    smoke = make_release(tmp_path)
    smoke["build_manifest_sha256"] = "old"
    (tmp_path / "QQExport-portable-0.1.1rc1.zip").write_bytes(b"changed")
    errors = gate.validate(tmp_path, smoke, "v0.1.1rc1", "0.1.1rc1", "synthetic")
    assert any("校验失败" in e for e in errors)
    assert any("冒烟证据" in e for e in errors)


@pytest.mark.parametrize("status", ["SKIP", "UNVERIFIED", "FAIL"])
def test_stable_never_accepts_skip(tmp_path, status):
    smoke = make_release(tmp_path, "0.1.1")
    evidence = {"source_commit": "synthetic", "checks": dict.fromkeys(gate.STABLE_CHECKS, "PASS")}
    evidence["checks"]["real_pdf"] = status
    assert gate.validate(tmp_path, smoke, "v0.1.1", "0.1.1", "synthetic", evidence)
    evidence["checks"]["real_pdf"] = "PASS"
    assert gate.validate(tmp_path, smoke, "v0.1.1", "0.1.1", "synthetic", evidence) == []


def test_mismatched_tag_and_commit_rejected(tmp_path):
    smoke = make_release(tmp_path)
    errors = gate.validate(tmp_path, smoke, "v0.1.0-pre.3", "0.1.1rc1", "different")
    assert any("标签" in e for e in errors) and any("提交" in e for e in errors)
