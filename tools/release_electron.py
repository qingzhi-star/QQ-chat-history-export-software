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
    if manifest.get("release_title")!=f"绮录QQ聊天记录导出软件 v{version}":
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
                    or report.get("integratedTools") is not True or report.get("errors")!=[]
                    or report.get("scroll",{}).get("frameP95",float("inf"))>1000/55
                    or report.get("sidebar",{}).get("p95",float("inf"))>1000/55):
                errors.append("正式版 GUI/无运行时/55FPS 本机采样验收未通过")
    return errors


def notes(manifest):
    version = manifest["version"]
    text = f"# 绮录QQ聊天记录导出软件 v{version}\n\n"
    text += "适用于 **Windows 11 x64**。Windows 10 和其他架构未列入本版已验证支持范围。\n\n"
    text += "## 下载与运行\n\n- `Qilu-Setup-"+version+"-x64.exe`：安装版，带卸载程序。\n"
    text += "- `Qilu-"+version.replace("rc","-rc.")+"-x64.zip`：便携版，完整解压后运行 `绮录QQ聊天记录导出软件.exe`。\n\n"
    text += "无需安装 Python 或 Node.js；请保留主程序旁的 DLL、locales 和 resources。PDF 由 Electron 内置打印引擎生成。\n\n"
    if version=="0.3.1":
        text += "## 本次修复\n\n- QQ 写库导致的快照忙自动重试，后台刷新失败保留可用聊天；真正解密失败仍明确提示。\n"
        text += "- 刷新准备不占用消息读取锁，旧账号结果不会覆盖新账号；重复消息页使用有界解析缓存。\n"
        text += "- 断网头像请求不阻塞消息读取，启动不再等待账号头像网络请求。\n"
        text += "- 按用户要求固定“更多功能”标题和右上角叉号，只滚动下方列表，移除底部关闭按钮；其余 UI 保持原样。\n\n"
        text += "离线导出需要本机已有该数据库的有效口令。曾在 QQ 登录过但从未取得密钥的历史账号，不能仅凭加密 `.db` 文件离线解密，本版不承诺此能力。\n\n"
    text += "## 本版功能\n\n- Electron 双栏界面，沿用绮录名称与 D 图标，圆角按钮和头像。\n"
    text += "- 自动准备与切换本机账号，会话按最新消息排序并更新。\n"
    text += "- 虚拟列表、行内表情、图片预览及系统默认查看器、引用卡片、文件名。\n"
    text += "- 整行选择、日期范围、TXT/HTML/PDF 导出。\n"
    text += "- 内置搜索、批量/增量导出、队列恢复、加密归档、资料库、缺失媒体检测、缓存设置和计划文件生成。\n\n"
    text += "## 验证与范围\n\n当前提交已完成自动测试、安装/启动/卸载、无系统 Python/Node 运行、冻结内核归档/多格式导出及源数据字节不变检查。"
    text += "滚动采样使用 200 个合成会话和十万条消息，结果仅代表验收机器；实际大库和冷图片加载速度取决于设备及缓存。"
    text += "计划功能生成默认禁用的 XML/JSON，由用户在 Windows 任务计划程序中导入启用。"
    text += "本版未配置代码签名证书。\n\n## SHA-256\n\n```text\n"
    text += "".join(f"{item['sha256']}  {item['name']}\n" for item in manifest["artifacts"])+"```\n"
    text += f"\n源码提交：`{manifest['source_commit']}`。\n"
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
