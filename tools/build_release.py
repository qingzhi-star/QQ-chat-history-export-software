"""T08：固定工具链、隔离环境、同源版本、可追溯的 Windows 构建。"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import venv
import zipfile

ROOT = Path(__file__).resolve().parent.parent
VERSION_START = "<!-- version:start -->"
VERSION_END = "<!-- version:end -->"


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def project_version(root: Path = ROOT) -> str:
    value = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    version = value["project"]["version"]
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:[a-zA-Z0-9.+-]*)?", version):
        raise ValueError("pyproject.toml 的版本格式无效")
    return version


def sync_readme(root: Path = ROOT, *, write: bool = False) -> None:
    path = root / "README.md"
    text = path.read_text(encoding="utf-8")
    replacement = f"{VERSION_START}\n当前源码版本：**{project_version(root)}**。\n{VERSION_END}"
    pattern = re.compile(re.escape(VERSION_START) + r".*?" + re.escape(VERSION_END), re.S)
    if len(pattern.findall(text)) != 1:
        raise ValueError("README 缺少唯一版本标记")
    updated = pattern.sub(lambda m: replacement, text)
    if updated != text:
        if not write:
            raise ValueError("README 版本不同步，请运行 --sync-version")
        path.write_text(updated, encoding="utf-8")


def check_toolchain(root: Path = ROOT, iscc: Path | None = None) -> dict:
    cfg = json.loads((root / "build-toolchain.json").read_text(encoding="utf-8"))
    expected = (root / ".python-version").read_text(encoding="ascii").strip()
    if (platform.python_version() != expected or sys.platform != cfg["platform"]
            or platform.machine().upper() != cfg["architecture"].upper()):
        raise ValueError(f"构建需要 Windows x64 / Python {expected}")
    if iscc is not None:
        for name, digest in cfg["inno_files"].items():
            candidate = iscc.parent / name
            if not candidate.is_file() or sha256(candidate) != digest:
                raise ValueError(f"Inno Setup {cfg['inno_setup']} 工具链哈希不符：{name}")
    return cfg


def tree_manifest(root: Path) -> list[dict]:
    entries = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("构建目录不允许符号链接")
        if path.is_file():
            entries.append({"path": path.relative_to(root).as_posix(),
                            "size": path.stat().st_size, "sha256": sha256(path)})
    return entries


def compare_manifests(first: dict, second: dict) -> dict:
    for key in ("version", "python", "requirements_sha256", "toolchain_sha256", "source_sha256"):
        if first[key] != second[key]:
            raise ValueError(f"不能比较不同构建输入：{key}")
    left = {entry["path"]: entry for entry in first["files"]}
    right = {entry["path"]: entry for entry in second["files"]}
    if left.keys() != right.keys():
        raise ValueError("重复构建目录结构不一致")
    return {"structure_equal": True, "files": len(left),
            "content_equal": all(left[name] == right[name] for name in left)}


def write_portable(bundle: Path, output: Path, epoch: int) -> None:
    # 排序与统一时间戳消除 ZIP 的目录遍历顺序、文件 mtime 差异。
    stamp = time.gmtime(max(315532800, epoch))[:6]
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for entry in tree_manifest(bundle):
            path = bundle / entry["path"]
            info = zipfile.ZipInfo(f"QQExport/{entry['path']}", date_time=stamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            zf.writestr(info, path.read_bytes(), compresslevel=9)


def write_version_file(path: Path, version: str) -> None:
    numbers = tuple(int(n) for n in re.match(r"(\d+)\.(\d+)\.(\d+)", version).groups()) + (0,)
    path.write_text(
        "VSVersionInfo(ffi=FixedFileInfo("
        f"filevers={numbers!r}, prodvers={numbers!r}, mask=0x3f, flags=0, OS=0x40004, "
        "fileType=1, subtype=0, date=(0, 0)), kids=[StringFileInfo([StringTable('080404B0', ["
        f"StringStruct('FileVersion', {version!r}), StringStruct('ProductVersion', {version!r}), "
        "StringStruct('ProductName', 'QQ聊天记录导出'), "
        "StringStruct('OriginalFilename', 'QQExport.exe')])]), "
        "VarFileInfo([VarStruct('Translation', [2052, 1200])])])", encoding="utf-8")


def run(command: list[str], log: Path, *, env: dict | None = None) -> None:
    with log.open("ab") as stream:
        result = subprocess.run(command, cwd=ROOT, env=env, stdout=stream, stderr=stream)
    if result.returncode:
        raise RuntimeError(f"构建步骤失败（退出码 {result.returncode}），详细输出见构建日志")


def source_identity(root: Path = ROOT) -> dict:
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=root).decode("utf-8").strip()

    files = git("ls-files", "-z").split("\0")
    digest = hashlib.sha256()
    for name in sorted(filter(None, files)):
        path = root / name
        if path.is_file():
            digest.update(name.encode("utf-8") + b"\0" + bytes.fromhex(sha256(path)))
    return {"source_commit": git("rev-parse", "HEAD"), "source_sha256": digest.hexdigest(),
            "source_dirty": bool(git("status", "--porcelain", "--untracked-files=no")),
            "source_epoch": int(git("show", "-s", "--format=%ct", "HEAD"))}


def build(output: Path, *, iscc: Path | None, wheelhouse: Path | None = None) -> dict:
    output = output.resolve()
    if output.exists():
        raise ValueError("输出目录已存在，请指定新的 --out 目录")
    check_toolchain(iscc=iscc)
    sync_readme()
    version = project_version()
    identity = source_identity()
    build_root = ROOT / "build"
    build_root.mkdir(exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    # 同卷临时产物；失败不替换已有输出，不污染上一次发布目录。
    # Python 3.14 的 Windows venv 启动器在部分含空格路径上不能启动；
    # 隔离工具放系统临时目录，产物仍在目标同卷暂存后整体发布。
    with tempfile.TemporaryDirectory(prefix="qqexport-build-") as work_str, \
            tempfile.TemporaryDirectory(prefix=".release-", dir=output.parent) as stage_str:
        work, stage = Path(work_str), Path(stage_str)
        log = build_root / "release-build.log"
        log.write_text("", encoding="utf-8")
        env_path = work / "venv"
        print("[BUILD] 创建隔离环境并校验锁定依赖", flush=True)
        venv.EnvBuilder(with_pip=True).create(env_path)
        python = str(env_path / "Scripts" / "python.exe")
        pip_args = [python, "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
                    "--require-hashes", "--only-binary=:all:", "-r", str(ROOT / "requirements-build.lock")]
        if wheelhouse is not None:
            pip_args += ["--no-index", "--find-links", str(wheelhouse.resolve())]
        run(pip_args, log)
        env = dict(os.environ, PYTHONHASHSEED="0", PYTHONUTF8="1",
                   SOURCE_DATE_EPOCH=str(identity["source_epoch"]))
        # 安装 wheel 而非 editable 包，确保冻结程序拿到同源版本的 METADATA。
        wheels = work / "wheels"
        run([python, "-m", "pip", "wheel", "--quiet", "--no-deps", "--no-build-isolation",
             "--wheel-dir", str(wheels), str(ROOT)], log, env=env)
        wheel = next(wheels.glob("qqexport-*.whl"))
        run([python, str(ROOT / "tools" / "check_wheel.py"), str(wheel)], log, env=env)
        run([python, "-m", "pip", "install", "--quiet", "--no-deps", str(wheel)], log, env=env)
        version_file = work / "version.txt"
        write_version_file(version_file, version)
        print("[BUILD] PyInstaller onedir", flush=True)
        run([python, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--onedir",
             "--name", "QQExport", "--distpath", str(stage), "--workpath", str(work / "pyinstaller"),
             "--specpath", str(work), "--hidden-import", "PIL._tkinter_finder",
             "--collect-submodules", "qqexport", "--copy-metadata", "qqexport",
             "--copy-metadata", "regex",
             "--version-file", str(version_file), str(ROOT / "tools" / "launch_gui.py")], log, env=env)
        bundle = stage / "QQExport"
        for name in ("LICENSE","NOTICE","README.md"):
            shutil.copy2(ROOT/name,bundle/name)
        for path in bundle.rglob("direct_url.json"):
            path.unlink()  # pip 本机安装路径不属于分发资产。
        artifacts = []
        portable = stage / f"QQExport-portable-{version}.zip"
        print("[BUILD] 绿色版与安装包", flush=True)
        write_portable(bundle, portable, identity["source_epoch"])
        artifacts.append(portable)
        if iscc is not None:
            run([str(iscc.resolve()), "/Q", f"/DMyAppVersion={version}",
                 f"/DSourceDir={bundle}", f"/O{stage}", str(ROOT / "tools" / "installer.iss")], log, env=env)
            artifacts.append(stage / f"QQExport-Setup-{version}.exe")
        manifest = {"schema_version": 1, "version": version, "python": platform.python_version(),
                    "release_title": f"qqexport v{version}", **identity,
                    "requirements_sha256": sha256(ROOT / "requirements-build.lock"),
                    "toolchain_sha256": sha256(ROOT / "build-toolchain.json"),
                    "files": tree_manifest(bundle),
                    "artifacts": [{"path": path.name, "size": path.stat().st_size,
                                   "sha256": sha256(path)} for path in artifacts]}
        (stage / "build-manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (stage / "SHA256SUMS").write_text("".join(
            f"{entry['sha256']}  {entry['path']}\n" for entry in manifest["artifacts"]), encoding="ascii")
        # TemporaryDirectory 清理空壳，已完成目录整体发布。
        stage.rename(output)
    print(f"[OK] 构建完成：{output.name}", flush=True)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "dist" / "release")
    parser.add_argument("--iscc", type=Path, default=Path(os.environ.get("LOCALAPPDATA", ""))
                        / "Programs" / "Inno Setup 6" / "ISCC.exe")
    parser.add_argument("--portable-only", action="store_true")
    parser.add_argument("--wheelhouse", type=Path, help="已下载的锁定 wheel，启用离线安装")
    parser.add_argument("--sync-version", action="store_true", help="从 pyproject 同步 README 后退出")
    parser.add_argument("--compare", type=Path, help="与另一份 build-manifest.json 比较重复构建")
    args = parser.parse_args(argv)
    try:
        if args.sync_version:
            sync_readme(write=True)
            print(f"[OK] 源码版本 {project_version()}")
            return 0
        manifest = build(args.out, iscc=None if args.portable_only else args.iscc,
                         wheelhouse=args.wheelhouse)
        if args.compare:
            other = json.loads(args.compare.read_text(encoding="utf-8"))
            print(json.dumps(compare_manifests(other, manifest), ensure_ascii=False))
        return 0
    except (ValueError, OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
