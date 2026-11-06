"""锁定 Python 内核，Electron 目录由 electron-builder 生成，安装/卸载使用 Inno Setup。"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import venv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))


def run(command, **kwargs):
    subprocess.run(list(map(str,command)),cwd=ROOT,check=True,**kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheelhouse",type=Path)
    parser.add_argument("--core-only",action="store_true")
    parser.add_argument("--electron-dist",type=Path,help="使用已校验的 Electron 运行库目录")
    parser.add_argument("--temp-dir",type=Path,help="仅本次构建子进程使用的临时目录")
    parser.add_argument("--iscc",type=Path,default=Path(os.environ.get("LOCALAPPDATA",""))/"Programs/Inno Setup 6/ISCC.exe")
    args = parser.parse_args()
    from tools.build_release import check_toolchain
    check_toolchain(iscc=args.iscc)
    version = tomllib.loads((ROOT/"pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    js_version = version.replace("rc","-rc.")
    package = ROOT/"desktop/package.json"
    data = json.loads(package.read_text(encoding="utf-8"))
    if data["version"]!=js_version:
        raise ValueError("Electron 版本与 pyproject.toml 不一致，请同步版本")
    out = ROOT/"dist"/f"electron-{version}"
    data["build"]["directories"]["output"] = f"../dist/electron-{version}"
    package.write_text(json.dumps(data,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    if args.temp_dir:
        args.temp_dir.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="qilu-core-build-",dir=args.temp_dir) as temporary:
        env = Path(temporary)/"env"
        venv.EnvBuilder(with_pip=True).create(env)
        python = env/"Scripts/python.exe"
        install = [python,"-m","pip","install","--quiet","--require-hashes","--only-binary=:all:","-r",ROOT/"requirements-build.lock"]
        if args.wheelhouse:
            install += ["--no-index","--find-links",args.wheelhouse.resolve()]
        run(install)
        wheels = Path(temporary)/"wheels"
        run([python,"-m","pip","wheel","--quiet","--no-deps","--no-build-isolation","--wheel-dir",wheels,ROOT])
        wheel = next(wheels.glob("qqexport*.whl"))
        run([python,ROOT/"tools/check_wheel.py",wheel])
        run([python,"-m","pip","install","--quiet","--no-deps",wheel])
        from tools.build_release import write_version_file
        version_file = Path(temporary)/"version.txt"
        write_version_file(version_file,version)
        run([python,"-m","PyInstaller","--noconfirm","--clean","--console","--onedir","--name","qilu-core",
             "--distpath",ROOT/"build/electron-core","--workpath",Path(temporary)/"work","--specpath",temporary,
             "--collect-submodules","qqexport","--collect-data","qqexport","--copy-metadata","qqexport",
             "--copy-metadata","regex","--hidden-import","PIL._tkinter_finder","--icon",ROOT/"qqexport/assets/qilu.ico",
             "--version-file",version_file,
             ROOT/"tools/launch_electron_core.py"])
    if args.core_only:
        return
    command = ["npm.cmd","run","dist"]
    if args.electron_dist:
        command += ["--",f"--config.electronDist={args.electron_dist.resolve()}"]
    build_env = dict(os.environ)
    if args.temp_dir:
        build_env.update(TEMP=str(args.temp_dir.resolve()),TMP=str(args.temp_dir.resolve()))
    subprocess.run(command,cwd=ROOT/"desktop",check=True,env=build_env)
    run([args.iscc,"/Q",f"/DMyAppVersion={version}",f"/DSourceDir={out/'win-unpacked'}",f"/O{out}",
         ROOT/"tools/electron-installer.iss"],env=build_env)
    artifacts = []
    for path in (out/f"Qilu-Setup-{version}-x64.exe",out/f"Qilu-{js_version}-x64.zip"):
        artifacts.append(dict(name=path.name,size=path.stat().st_size,sha256=hashlib.file_digest(path.open("rb"),"sha256").hexdigest()))
    manifest = dict(version=version,electron=data["devDependencies"]["electron"],
        source_commit=subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip(),
        source_dirty=bool(subprocess.check_output(["git","status","--porcelain"],cwd=ROOT,text=True)),artifacts=artifacts)
    (out/"electron-build.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(manifest,ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
