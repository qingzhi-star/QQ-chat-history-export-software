"""Electron 发行门：匹配源码、完整目录、分发哈希和同构建 Windows 11 验收。"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from tools import build_release as br

CHECKS = {"portable_unicode_spaces","install_launch","uninstall_clean","without_system_runtime",
          "gui_regression","real_pdf","source_readonly","frozen_pipeline"}


def check_source(tag=None):
    from tools.build_privacy import render
    privacy_html,privacy_metadata=render()
    if ((br.ROOT/"desktop/renderer/privacy.html").read_text(encoding="utf-8")!=privacy_html
            or (br.ROOT/"desktop/privacy-policy.json").read_text(encoding="utf-8")!=privacy_metadata):
        raise ValueError("隐私条款派生文件不同步")
    version = br.project_version()
    package = json.loads((br.ROOT/"desktop/package.json").read_text(encoding="utf-8"))
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:rc[1-9]\d*)?",version):
        raise ValueError("项目版本无效")
    if package["version"]!=version.replace("rc","-rc.") or tag is not None and tag!="v"+version:
        raise ValueError("标签、Python 版本和 npm 版本不一致")
    br.sync_readme()
    return version


def validate(release,smoke,version,commit,source_sha):
    errors = []
    manifest_path = release/"electron-build.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (manifest.get("schema_version")!=2 or manifest.get("version")!=version or manifest.get("source_commit")!=commit
            or manifest.get("source_sha256")!=source_sha or manifest.get("source_dirty") is not False):
        errors.append("构建必须来自当前干净提交和源码指纹")
    if manifest.get("supported_platforms")!=["windows11-x64"]:
        errors.append("本轮正式版仅验证 Windows 11 x64，不能扩大支持范围")
    if manifest.get("release_title")!=f"v{version}":
        errors.append("发行标题不一致")
    expected = {f"Qilu-Setup-{version}-x64.exe",f"Qilu-{version.replace('rc','-rc.')}-x64.zip"}
    assets = manifest.get("artifacts",[])
    if len(assets)!=2 or {item["name"] for item in assets}!=expected:
        errors.append("仅允许同版本安装包和完整便携 ZIP 两项资产")
    for item in assets:
        if item["name"] not in expected:
            continue
        file = release/item["name"]
        if not file.is_file() or file.stat().st_size!=item["size"] or br.sha256(file)!=item["sha256"]:
            errors.append("分发资产哈希或大小不匹配")
    if br.tree_manifest(release/"win-unpacked")!=manifest.get("files"):
        errors.append("Electron 完整目录与构建清单不一致")
    for key,name in (("requirements_sha256","requirements-build.lock"),("npm_lock_sha256","desktop/package-lock.json"),
                     ("toolchain_sha256","build-toolchain.json")):
        if manifest.get(key)!=br.sha256(br.ROOT/name):
            errors.append("锁定依赖或工具链与构建不一致")
    expected_sums = "".join(f"{item['sha256']}  {item['name']}\n" for item in assets)
    if (release/"SHA256SUMS").read_text(encoding="ascii")!=expected_sums:
        errors.append("SHA256SUMS 与清单不符")
    if (smoke.get("pass") is not True or smoke.get("version")!=version or smoke.get("source_commit")!=commit
            or smoke.get("build_manifest_sha256")!=br.sha256(manifest_path)
            or any(smoke.get("checks",{}).get(check) is not True for check in CHECKS)):
        errors.append("缺少当前构建绑定的完整验收，未运行或跳过均不接受")
    if "rc" not in version:
        if smoke.get("platform",{}).get("release")!="11" or smoke.get("platform",{}).get("machine","").lower() not in {"amd64","x86_64"}:
            errors.append("正式版需要 Windows 11 x64 的当前构建验收")
        for name in ("portable_gui","installed_gui"):
            report = smoke.get(name,{})
            if (report.get("pass") is not True or report.get("systemRuntimeRemoved") is not True
                    or report.get("privacyGate") is not True or report.get("privacyDeclined") is not True
                    or report.get("integratedTools") is not True or report.get("errors")!=[]
                    or report.get("scroll",{}).get("frameP95",float("inf"))>1000/55
                    or report.get("sidebar",{}).get("p95",float("inf"))>1000/55):
                errors.append("正式版 GUI/无运行时/55FPS 本机采样验收未通过")
    return errors


def notes(manifest):
    version=manifest["version"]
    specific=br.ROOT/"docs/releases"/(version+".md")
    if specific.is_file():
        return specific.read_text(encoding="utf-8")
    text="## 下载与运行\n\n"
    text+=f"- 安装版：`Qilu-Setup-{version}-x64.exe`，包含卸载程序。\n"
    text+=f"- 便携版：`Qilu-{version.replace('rc','-rc.')}-x64.zip`，完整解压后运行 `绮录QQ聊天记录导出软件.exe`。\n"
    text+="- 无需 Python 或 Node.js；保留 DLL、`locales` 和 `resources`。\n\n"
    text+="## 支持的系统范围\n\n已验证 Windows 11 x64。Windows 10、其他架构和其他操作系统未列入已验证支持范围。\n"
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("release",type=Path,nargs="?")
    parser.add_argument("--smoke",type=Path)
    parser.add_argument("--tag")
    parser.add_argument("--check-source",action="store_true")
    args = parser.parse_args()
    version = check_source(args.tag)
    if args.check_source:
        print(f"[OK] Electron source version {version}")
        return
    if not args.release or not args.smoke:
        parser.error("必须提供 release 和 --smoke")
    identity = br.source_identity()
    if identity["source_dirty"]:
        raise ValueError("源码不干净")
    errors = validate(args.release,json.loads(args.smoke.read_text(encoding="utf-8")),version,
                      identity["source_commit"],identity["source_sha256"])
    if errors:
        raise ValueError("；".join(errors))
    manifest = json.loads((args.release/"electron-build.json").read_text(encoding="utf-8"))
    (args.release/"release-notes.md").write_text(notes(manifest),encoding="utf-8")
    print(f"[OK] Electron v{version} release gate passed")


if __name__=="__main__":
    main()
