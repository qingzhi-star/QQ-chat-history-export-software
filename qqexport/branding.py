"""展示品牌与内部数据目录相互独立，保留已有账号缓存。"""
from pathlib import Path
import tkinter as tk

APP_NAME = "绮录QQ聊天记录导出软件"
ASSETS = Path(__file__).with_name("assets")


def set_app_id():
    import os
    if os.name=="nt":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("qingzhi-star.Qilu")
        except (OSError,AttributeError):
            pass


def apply_icon(window):
    path = ASSETS/"qilu.png"
    if path.is_file():
        window._brand_icon = tk.PhotoImage(master=window,file=str(path))
        window.iconphoto(True,window._brand_icon)
