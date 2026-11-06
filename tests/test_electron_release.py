import json
import pytest
from tools import release_electron as gate,build_release as br


def release(tmp_path):
    bundle = tmp_path/"win-unpacked"
    bundle.mkdir()
    (bundle/"synthetic.exe").write_bytes(b"synthetic")
    assets = []
    for name in ("Qilu-Setup-0.3.0-x64.exe","Qilu-0.3.0-x64.zip"):
        path = tmp_path/name
        path.write_bytes(b"synthetic asset")
        assets.append(dict(name=name,size=path.stat().st_size,sha256=br.sha256(path)))
    manifest = dict(schema_version=2,version="0.3.0",source_commit="synthetic",source_sha256="source",source_dirty=False,
        supported_platforms=["windows11-x64"],release_title="v0.3.0",artifacts=assets,files=br.tree_manifest(bundle),
        requirements_sha256=br.sha256(br.ROOT/"requirements-build.lock"),npm_lock_sha256=br.sha256(br.ROOT/"desktop/package-lock.json"),
        toolchain_sha256=br.sha256(br.ROOT/"build-toolchain.json"))
    path = tmp_path/"electron-build.json"
    path.write_text(json.dumps(manifest),encoding="utf-8")
    (tmp_path/"SHA256SUMS").write_text("".join(f"{item['sha256']}  {item['name']}\n" for item in assets),encoding="ascii")
    gui = dict(pass_=True,systemRuntimeRemoved=True,integratedTools=True,privacyGate=True,privacyDeclined=True,errors=[],scroll=dict(frameP95=17.5),sidebar=dict(p95=17.5))
    gui["pass"] = gui.pop("pass_")
    return dict(version="0.3.0",source_commit="synthetic",build_manifest_sha256=br.sha256(path),
        platform=dict(release="11",machine="AMD64"),checks=dict.fromkeys(gate.CHECKS,True),
        portable_gui=gui.copy(),installed_gui=gui.copy(),**{"pass":True})


def test_electron_stable_requires_current_bound_complete_evidence(tmp_path):
    smoke = release(tmp_path)
    assert gate.validate(tmp_path,smoke,"0.3.0","synthetic","source")==[]
    smoke["build_manifest_sha256"]="old"
    assert any("验收" in value for value in gate.validate(tmp_path,smoke,"0.3.0","synthetic","source"))


@pytest.mark.parametrize("check",sorted(gate.CHECKS))
def test_electron_release_rejects_unverified_checks(tmp_path,check):
    smoke = release(tmp_path)
    smoke["checks"][check]="SKIP"
    assert gate.validate(tmp_path,smoke,"0.3.0","synthetic","source")


def test_electron_release_rejects_changed_asset_or_bundle(tmp_path):
    smoke = release(tmp_path)
    (tmp_path/"Qilu-0.3.0-x64.zip").write_bytes(b"changed")
    (tmp_path/"win-unpacked/synthetic.exe").write_bytes(b"changed")
    errors = gate.validate(tmp_path,smoke,"0.3.0","synthetic","source")
    assert any("哈希" in value for value in errors) and any("完整目录" in value for value in errors)


def test_electron_release_rejects_os_scope_expansion_and_slow_frames(tmp_path):
    smoke = release(tmp_path)
    smoke["platform"]["release"]="10"
    smoke["portable_gui"]["scroll"]["frameP95"]=30
    errors = gate.validate(tmp_path,smoke,"0.3.0","synthetic","source")
    assert any("Windows 11" in value for value in errors) and any("55FPS" in value for value in errors)
