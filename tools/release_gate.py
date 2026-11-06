"""版本/标签、分发资产哈希、冒烟绑定和正式版人工证据发布门。"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools import build_release as br

SMOKE_CHECKS = {"portable_unicode_spaces", "missing_internal_diagnostic",
                "per_user_install_launch", "uninstall_clean"}
STABLE_CHECKS = {"windows10", "windows11", "gui_regression", "real_pdf", "performance", "source_readonly"}


def validate(release: Path, smoke: dict, tag: str, version: str, commit: str,
             evidence: dict | None = None) -> list[str]:
    errors = []
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:rc[1-9]\d*)?", version) or tag != "v" + version:
        errors.append("标签必须严格匹配 project.version，候选版使用 rcN")
    manifest_path = release / "build-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (manifest.get("version") != version or manifest.get("source_commit") != commit
            or manifest.get("source_dirty") is not False):
        errors.append("构建版本/提交不匹配或源码不干净")
    if manifest.get("release_title") != f"qqexport v{version}":
        errors.append("Release 标题版本不一致")
    wanted = {f"QQExport-portable-{version}.zip", f"QQExport-Setup-{version}.exe"}
    assets = manifest.get("artifacts", [])
    if {entry["path"] for entry in assets} != wanted or len(assets) != 2:
        errors.append("Release 只允许同版本安装包和绿色版")
    for entry in assets:
        name = entry["path"]
        if name not in wanted:
            continue
        path = release / name
        if not path.is_file() or path.stat().st_size != entry["size"] or br.sha256(path) != entry["sha256"]:
            errors.append("分发资产校验失败")
    if (smoke.get("ok") is not True or smoke.get("version") != version
            or smoke.get("build_manifest_sha256") != br.sha256(manifest_path)
            or any(smoke.get("checks", {}).get(key) is not True for key in SMOKE_CHECKS)):
        errors.append("缺少当前构建绑定的完整冒烟证据")
    if br.tree_manifest(release / "QQExport") != manifest.get("files"):
        errors.append("绿色版目录与清单不符")
    expected_sums = "".join(f"{entry['sha256']}  {entry['path']}\n" for entry in assets)
    if (release / "SHA256SUMS").read_text(encoding="ascii") != expected_sums:
        errors.append("SHA256SUMS 与清单不符")
    if "rc" not in version:
        evidence = evidence or {}
        if evidence.get("source_commit") != commit or any(
                evidence.get("checks", {}).get(key) != "PASS" for key in STABLE_CHECKS):
            errors.append("正式版缺少同提交的真机/性能验收证据；SKIP 不算 PASS")
    return errors


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("release", type=Path)
    parser.add_argument("--smoke", type=Path, required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--evidence", type=Path)
    args = parser.parse_args(argv)
    try:
        evidence = json.loads(args.evidence.read_text(encoding="utf-8")) if args.evidence else None
        if evidence is None and os.environ.get("RELEASE_EVIDENCE_JSON"):
            evidence = json.loads(os.environ["RELEASE_EVIDENCE_JSON"])
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=br.ROOT, text=True).strip()
        errors = validate(args.release, json.loads(args.smoke.read_text(encoding="utf-8")),
                          args.tag, br.project_version(), commit, evidence)
        if errors:
            for error in errors:
                print("[FAIL] " + error)
            return 1
        notes = f"# qqexport {args.tag}\n\n本机处理，只读源库。\n\n## SHA-256\n\n```text\n"
        notes += (args.release / "SHA256SUMS").read_text(encoding="ascii") + "```\n"
        (args.release / "release-notes.md").write_text(notes, encoding="utf-8")
        print("[OK] 版本、资产与验收门通过")
        return 0
    except (ValueError, OSError, KeyError) as exc:
        print(f"[FAIL] 发布证据不可用（{type(exc).__name__}）")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
