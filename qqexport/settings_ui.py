"""本机设置；配置损坏只在明确恢复后写入。"""
from dataclasses import replace
import tkinter as tk
from tkinter import ttk,filedialog,messagebox
from qqexport import config


class SettingsDialog(tk.Toplevel):
    def __init__(self,app):
        super().__init__(app.root)
        self.app = app
        self.title("本机设置")
        self.transient(app.root)
        body = ttk.Frame(self,padding=16)
        body.pack(fill="both",expand=True)
        ttk.Label(body,text="默认输出目录").pack(anchor="w")
        self.output = tk.StringVar(value=app.last_out)
        ttk.Entry(body,textvariable=self.output,width=65).pack(fill="x",pady=5)
        def browse():
            path = filedialog.askdirectory(parent=self,initialdir=self.output.get() or None)
            if path:
                self.output.set(path)
        ttk.Button(body,text="选择目录…",command=browse).pack(anchor="w")
        ttk.Label(body,text="默认导出格式（可多选）").pack(anchor="w",pady=(12,4))
        self.formats = {name:tk.BooleanVar(value=name in app.preferences.formats) for name in ("txt","html","pdf")}
        for name,var in self.formats.items():
            ttk.Checkbutton(body,text=name.upper(),variable=var).pack(anchor="w")
        ttk.Label(body,text="缩略图磁盘上限（MiB，0 表示不保存；默认 2048，最多 16384）").pack(anchor="w",pady=(12,4))
        self.limit = tk.StringVar(value=str(app.preferences.thumbnail_mb))
        ttk.Spinbox(body,from_=0,to=16384,textvariable=self.limit,width=12).pack(anchor="w")
        self.density = tk.StringVar(value=app.preferences.density)
        ttk.Label(body,text="消息密度").pack(anchor="w",pady=(12,4))
        ttk.Radiobutton(body,text="标准（默认）",variable=self.density,value="normal").pack(anchor="w")
        ttk.Radiobutton(body,text="紧凑",variable=self.density,value="compact").pack(anchor="w")
        ttk.Label(body,text="上次账号仅作标记；多个账号始终由你明确选择。\n索引默认仅在内存；搜索窗口可清除索引和关键词历史。",wraplength=520).pack(anchor="w",pady=12)
        controls = ttk.Frame(body)
        controls.pack(fill="x")
        for label,action in [("搜索/清除历史",app._show_search),("清理缩略图",app._clear_thumbnails),("清空资料库",lambda:app._library_action("clear_library"))]:
            ttk.Button(controls,text=label,command=action).pack(side="left",padx=3)
        if app._config_failed:
            ttk.Button(body,text="备份并恢复损坏配置…",command=app._recover_settings).pack(pady=6)
        ttk.Button(body,text="保存设置",command=self.save).pack(anchor="e",pady=(15,0))

    def save(self):
        if self.app._config_failed:
            messagebox.showinfo("配置未恢复","请先备份并恢复损坏配置，再保存设置。",parent=self)
            return
        try:
            preferences = replace(self.app.preferences,last_out=self.output.get().strip() or None,
                formats=[name for name,var in self.formats.items() if var.get()],
                density=self.density.get(),thumbnail_mb=int(self.limit.get()))
            preferences.save()
        except (ValueError,config.ConfigError):
            messagebox.showerror("设置无效","请至少选择一种格式，缓存上限需为 0–16384 的整数。配置不可写时请检查或恢复配置。",parent=self)
            return
        self.app.preferences = preferences
        self.app.last_out = preferences.last_out or self.app.last_out
        self.app.thumbnail_limit = preferences.thumbnail_mb*1024**2
        self.app.virtual.density = preferences.density
        self.app.virtual.schedule()
        def trim():
            from qqexport.thumbnail_cache import ThumbnailCache
            cache = ThumbnailCache(self.app.thumbnail_limit)
            with cache.lease():
                cache._trim()
        self.app.tasks.submit(trim,lambda _:None,self.app._error)
        self.destroy()
