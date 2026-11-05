"""打包冒烟入口：构建真实主界面，但不发现账号、不读取 QQ、不联网。"""
from __future__ import annotations

import json
from pathlib import Path


def run_smoke(report: Path) -> int:
    root = None
    result = {"ok": False}
    try:
        import tkinter as tk
        from qqexport import __version__
        from qqexport.gui import App

        class SmokeApp(App):
            def _load_chats(self):
                self.count_label.configure(text="合成启动检查")

        root = tk.Tk()
        root.withdraw()
        app = SmokeApp(root)
        root.update()
        assert app.export_btn.winfo_exists() and app.text.winfo_exists()
        result.update(ok=True, version=__version__, tcl=root.tk.call("info", "patchlevel"),
                      account_discovery=False, window_title=root.title())
        return 0
    except Exception as exc:
        result["error"] = type(exc).__name__
        return 1
    finally:
        if root is not None:
            root.destroy()
        report.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
