"""Windows Tcl 初始化的短暂文件占用重试；永久缺失仍明确失败。"""
import gc
import time
import tkinter as tk


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
