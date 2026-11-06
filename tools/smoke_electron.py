"""验证完整便携目录、安装器、卸载器，资料和测试目录均独立于本人配置。"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import time
import zipfile
import platform

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
NAME = "绮录QQ聊天记录导出软件.exe"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("distribution",type=Path)
    parser.add_argument("--work",type=Path,required=True,help="干净的标准用户测试目录")
    parser.add_argument("--report",type=Path,default=ROOT/"build/electron-distribution-smoke.json")
    args = parser.parse_args()
    distribution,work = args.distribution.resolve(),args.work.resolve()
    if work.exists():
        raise ValueError("测试目录已经存在，请使用新目录")
    work.mkdir(parents=True)
    manifest = json.loads((distribution/"electron-build.json").read_text(encoding="utf-8"))
    installer = distribution/next(item["name"] for item in manifest["artifacts"] if item["name"].endswith(".exe"))
    archive = distribution/next(item["name"] for item in manifest["artifacts"] if item["name"].endswith(".zip"))
    import hashlib
    for item in manifest["artifacts"]:
        with (distribution/item["name"]).open("rb") as stream:
            assert hashlib.file_digest(stream,"sha256").hexdigest()==item["sha256"]
    portable = work/"便携版 中文路径"
    portable.mkdir()
    with zipfile.ZipFile(archive) as bundle:
        for item in bundle.infolist():
            assert (portable/item.filename).resolve().is_relative_to(portable)
        bundle.extractall(portable)
    executable = portable/NAME
    assert executable.is_file()
    assert executable.with_name(executable.stem+"_wer.dll").is_file()
    assert (portable/"resources/core/qilu-core.exe").is_file()
    env = dict(os.environ,QILU_PACKAGED_EXE=str(executable),QILU_PYTHON=sys.executable)
    subprocess.run(["npm.cmd","run","test:ui"],cwd=ROOT/"desktop",env=env,check=True)
    gui = json.loads((ROOT/"build/electron-packaged-smoke.json").read_text(encoding="utf-8"))
    report = dict(version=manifest["version"],source_commit=manifest["source_commit"],portable=True,
        build_manifest_sha256=hashlib.file_digest((distribution/"electron-build.json").open("rb"),"sha256").hexdigest(),
        platform=dict(system=platform.system(),release=platform.release(),machine=platform.machine()),portable_gui=gui)
    from tools.verify_electron_core import verify
    report["frozen_pipeline"] = verify(executable,work/"frozen-pipeline")
    if os.name=="nt":
        import winreg
        registry = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\qingzhi-star.Qilu.Desktop_is1"
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,registry):
                raise RuntimeError("已有本人安装记录，不覆盖；请使用隔离测试账号")
        except FileNotFoundError:
            pass
        installed = work/"installed"
        # 与用户从桌面运行相同：安装器也在普通用户目录，不继承开发工作区 ACL。
        local_installer = work/"Setup.exe"
        shutil.copy2(installer,local_installer)
        # 使用真实最终安装器；不更改系统 TEMP，不创建本人桌面快捷方式。
        subprocess.run([str(local_installer),"/VERYSILENT","/SUPPRESSMSGBOXES","/NORESTART","/SP-",
                        f"/DIR={installed}","/TASKS="],check=True,timeout=120)
        assert (installed/NAME).is_file()
        uninstall = installed/"unins000.exe"
        assert uninstall.is_file()
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,registry) as key:
            assert "绮录" in winreg.QueryValueEx(key,"DisplayName")[0]
        sentinel = work/"home"/"preserved.txt"
        sentinel.parent.mkdir(parents=True,exist_ok=True)
        sentinel.write_text("synthetic settings",encoding="utf-8")
        launch_env = dict(env,QILU_PACKAGED_EXE=str(installed/NAME))
        subprocess.run(["npm.cmd","run","test:ui"],cwd=ROOT/"desktop",env=launch_env,check=True)
        report["installed_gui"] = json.loads((ROOT/"build/electron-packaged-smoke.json").read_text(encoding="utf-8"))
        subprocess.run([str(uninstall),"/VERYSILENT","/SUPPRESSMSGBOXES","/NORESTART"],check=True,timeout=120)
        deadline = time.monotonic()+30
        while (installed/NAME).exists() and time.monotonic()<deadline:
            time.sleep(.1)
        assert not (installed/NAME).exists()
        assert sentinel.read_text(encoding="utf-8")=="synthetic settings"
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,registry):
                raise AssertionError("卸载登记未移除")
        except FileNotFoundError:
            pass
        report.update(install=True,launch=True,uninstall=True)
    report["checks"] = dict(portable_unicode_spaces=report["portable"],install_launch=report.get("install") and report.get("launch"),
        uninstall_clean=report.get("uninstall"),without_system_runtime=gui.get("systemRuntimeRemoved") is True,
        gui_regression=gui.get("pass") is True and report.get("installed_gui",{}).get("pass") is True,
        real_pdf=report["frozen_pipeline"]["real_pdf"],source_readonly=report["frozen_pipeline"]["source_readonly"],
        frozen_pipeline=all(report["frozen_pipeline"].get(key) is True for key in ("archive_decrypt","search","txt_html_export","without_system_runtime")))
    report["pass"] = True
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False))


if __name__=="__main__":
    main()
