"""PyInstaller GUI entry point.

Tcl/Tk shipped by PyInstaller fails to initialize when the executable path
contains spaces. Copy the runtime data to a space-free local cache and point
TCL_LIBRARY/TK_LIBRARY there before tkinter starts.
"""
from __future__ import annotations

import ctypes
import os
import shutil
import sys
import tempfile
from pathlib import Path


def _short_path(path: Path) -> Path:
    try:
        buf = ctypes.create_unicode_buffer(1024)
        if ctypes.windll.kernel32.GetShortPathNameW(str(path), buf, 1024):
            return Path(buf.value)
    except Exception:
        pass
    return path


def _prepare_tcl() -> None:
    if not getattr(sys, "frozen", False):
        return
    base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    cache = Path(tempfile.gettempdir()) / "QQExportRuntime"
    for name in ("_tcl_data", "_tk_data"):
        src = base / name
        dst = cache / name
        if not src.is_dir():
            continue
        if not dst.exists():
            try:
                shutil.copytree(src, dst)
            except OSError:
                continue
    tcl = _short_path(cache / "_tcl_data")
    tk = _short_path(cache / "_tk_data")
    if tcl.is_dir():
        os.environ["TCL_LIBRARY"] = str(tcl)
    if tk.is_dir():
        os.environ["TK_LIBRARY"] = str(tk)


_prepare_tcl()

from qqexport.gui import run_gui  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(run_gui())
