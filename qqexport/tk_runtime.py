"""Windows Tcl 初始化的短暂文件占用重试；永久缺失仍明确失败。"""
import gc
import time
import tkinter as tk


def prepare_frozen():
    """兼容工具窗口沿用发行包的 Tcl 数据准备，不影响 Electron 渲染进程。"""
    import os
    import sys
    import tempfile
    import shutil
    from pathlib import Path
    if not getattr(sys,"frozen",False):
        return
    base = Path(getattr(sys,"_MEIPASS",Path(sys.executable).parent))
    cache = Path(tempfile.gettempdir())/"QQExportRuntime"
    for name,variable in (("_tcl_data","TCL_LIBRARY"),("_tk_data","TK_LIBRARY")):
        source,target = base/name,cache/name
        if not source.is_dir():
            continue
        if not target.exists():
            try:
                shutil.copytree(source,target)
            except OSError:
                continue
        import ctypes
        buffer = ctypes.create_unicode_buffer(32768)
        if ctypes.windll.kernel32.GetShortPathNameW(str(target),buffer,len(buffer)):
            target = Path(buffer.value)
        os.environ[variable] = str(target)


def create_root():
    from qqexport.branding import set_app_id
    set_app_id()
    # 上一个已销毁 Tk 的循环引用在创建工作线程前由主线程释放。
    gc.collect()
    for attempt in range(3):
        try:
            return tk.Tk()
        except tk.TclError:
            if attempt == 2:
                raise
            # 失败的部分解释器只能在主线程清理，再进行下一次初始化。
            gc.collect()
            time.sleep(.05*(attempt+1))
