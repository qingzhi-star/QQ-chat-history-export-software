"""版本以源码的 pyproject.toml 或已安装包的构建元数据为准。"""
from importlib.metadata import version
from pathlib import Path
import sys
import tomllib

_project = Path(__file__).resolve().parent.parent / "pyproject.toml"
if not getattr(sys, "frozen", False) and _project.is_file():
    __version__ = tomllib.loads(_project.read_text(encoding="utf-8"))["project"]["version"]
else:
    # Wheel 与冻结程序不依赖源码目录；构建时打包同源的 dist-info。
    __version__ = version("qqexport")
