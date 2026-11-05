"""T09：含空格/中文路径的绿色版、缺失运行库、隔离身份安装/卸载冒烟。"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from tools import build_release as br


def wait_until(predicate, timeout=15):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.1)
    return False


def registry_exists() -> bool:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Uninstall\QQExportSmokeTests_is1",
                0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY):
            return True
    except FileNotFoundError:
        return False


def missing_runtime(exe: Path, env: dict) -> bool:
    """只读取并关闭本次子进程的启动错误窗口，不操作其他应用。"""
    user = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user.EnumChildWindows.argtypes = [wintypes.HWND, callback_type, wintypes.LPARAM]
    user.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    texts = []
    with subprocess.Popen([str(exe)], env=env, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL) as proc:
        @callback_type
        def child(hwnd, _):
            buf = ctypes.create_unicode_buffer(user.GetWindowTextLengthW(hwnd) + 1)
            user.GetWindowTextW(hwnd, buf, len(buf))
            texts.append(buf.value)
            return True

        @callback_type
        def window(hwnd, _):
            pid = wintypes.DWORD()
            user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == proc.pid:
                child(hwnd, 0)
                user.EnumChildWindows(hwnd, child, 0)
                user.PostMessageW(hwnd, 0x0010, 0, 0)
            return True

        def check():
            user.EnumWindows(window, 0)
            return proc.poll() is not None

        try:
            wait_until(check, 15)
        finally:
            if proc.poll() is None:
                proc.terminate()
            proc.wait(timeout=10)
        return proc.returncode != 0 and "_internal" in " ".join(texts)


def launch(exe: Path, report: Path, env: dict, version: str) -> None:
    proc = subprocess.run([str(exe), "--smoke-test", str(report)], env=env,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=40)
    result = json.loads(report.read_text(encoding="utf-8"))
    if proc.returncode or not result.get("ok") or result.get("version") != version:
        raise RuntimeError("应用启动/版本冒烟失败")


def smoke(release: Path, iscc: Path) -> dict:
    manifest = json.loads((release / "build-manifest.json").read_text(encoding="utf-8"))
    version = manifest["version"]
    if registry_exists():
        raise RuntimeError("已有同名冒烟安装，拒绝覆盖")
    br.check_toolchain(iscc=iscc)
    result = {"schema_version": 1, "version": version, "checks": {}}
    with tempfile.TemporaryDirectory(prefix="qqexport-smoke-") as temp:
        base = Path(temp)
        env = dict(os.environ, QQEXPORT_HOME=str(base / "home"), LOCALAPPDATA=str(base / "local"),
                   TEMP=str(base / "tmp"), TMP=str(base / "tmp"))
        (base / "tmp").mkdir()
        bundle = base / "绿色版 with spaces" / "QQExport"
        shutil.copytree(release / "QQExport", bundle)
        launch(bundle / "QQExport.exe", base / "portable.json", env, version)
        result["checks"]["portable_unicode_spaces"] = True
        broken = base / "broken"
        broken.mkdir()
        shutil.copy2(bundle / "QQExport.exe", broken / "QQExport.exe")
        if not missing_runtime(broken / "QQExport.exe", env):
            raise RuntimeError("缺少 _internal 时未观察到明确错误")
        result["checks"]["missing_internal_diagnostic"] = True
        # 编译同一安装脚本，只有测试 AppId/显示名不同，避免覆盖用户安装和快捷方式。
        br.run([str(iscc), "/Q", "/DSmokeBuild=1", f"/DMyAppVersion={version}",
                f"/DSourceDir={bundle}", f"/O{base}", str(ROOT / "tools/installer.iss")],
               base / "compiler.log")
        setup = base / f"QQExport-Setup-{version}.exe"
        installed = base / "安装目录 with spaces"
        shortcut = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/QQExport Smoke Test.lnk"
        if shortcut.exists():
            raise RuntimeError("已有同名快捷方式，拒绝覆盖")
        try:
            subprocess.run([str(setup), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
                            "/SP-", "/TASKS=", f"/DIR={installed}"], check=True, timeout=60)
            if not registry_exists():
                raise RuntimeError("当前用户卸载登记缺失")
            launch(installed / "QQExport.exe", base / "installed.json", env, version)
            result["checks"]["per_user_install_launch"] = True
        finally:
            uninstaller = installed / "unins000.exe"
            if uninstaller.is_file():
                subprocess.run([str(uninstaller), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
                               check=True, timeout=60)
                if not wait_until(lambda: not installed.exists() and not registry_exists()):
                    raise RuntimeError("卸载后目录或登记残留")
        if shortcut.exists():
            raise RuntimeError("卸载后快捷方式残留")
        result["checks"]["uninstall_clean"] = True
    result["ok"] = True
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("release", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--iscc", type=Path, default=Path(os.environ.get("LOCALAPPDATA", ""))
                        / "Programs/Inno Setup 6/ISCC.exe")
    args = parser.parse_args(argv)
    try:
        result = smoke(args.release.resolve(), args.iscc.resolve())
    except Exception as exc:
        result = {"ok": False, "error": type(exc).__name__}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
