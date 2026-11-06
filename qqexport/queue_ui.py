"""多会话选择、队列进度、取消/重试及本机恢复。"""
from __future__ import annotations
from collections import Counter
from pathlib import Path
import queue
import tkinter as tk
from tkinter import ttk,messagebox,filedialog

from qqexport import config
from qqexport.export import orchestrator
from qqexport.export.jobs import ExportJob,JobError,account_hash
from qqexport.gui import FormatDialog


class BatchDialog(tk.Toplevel):
    def __init__(self,app):
        super().__init__(app.root)
        self.app = app
        self.session,self.infos = app.session,list(app.infos)
        self.title("批量导出会话")
        self.geometry("680x580")
        ttk.Label(self,text="Ctrl / Shift 多选；主界面也可 Ctrl 点击会话。空会话将跳过。").pack(padx=10,pady=8)
        self.tree = ttk.Treeview(self,columns=("title","count"),show="headings",selectmode="extended")
        self.tree.heading("title",text="会话")
        self.tree.heading("count",text="消息数")
        self.tree.column("title",width=480)
        self.tree.pack(fill="both",expand=True,padx=10)
        for i,info in enumerate(self.infos):
            self.tree.insert("","end",iid=str(i),values=(info.title,info.messages))
            if info.chat_id in app.batch_selected:
                self.tree.selection_add(str(i))
        self.remember = tk.BooleanVar(value=False)
        self.manifest = tk.BooleanVar(value=True)
        self.incremental = tk.BooleanVar(value=False)
        self.rebuild = tk.BooleanVar(value=False)
        ttk.Checkbutton(self,text="保留队列以便重启后恢复（仅会话标识与输出参数）",variable=self.remember).pack(anchor="w",padx=10,pady=5)
        ttk.Checkbutton(self,text="生成导出清单和文件校验值",variable=self.manifest).pack(anchor="w",padx=10)
        ttk.Checkbutton(self,text="增量备份（保存明文消息快照与媒体，后续只追加新消息）",variable=self.incremental,
                        command=self._incremental_mode).pack(anchor="w",padx=10,pady=4)
        self.rebuild_button = ttk.Checkbutton(self,text="全量重建已有增量备份（覆盖原导出）",variable=self.rebuild,state="disabled")
        self.rebuild_button.pack(anchor="w",padx=10)
        buttons = ttk.Frame(self,padding=10)
        buttons.pack(fill="x")
        ttk.Button(buttons,text="全选",command=lambda:self.tree.selection_set(self.tree.get_children())).pack(side="left")
        ttk.Button(buttons,text="导出选中",command=self.selected).pack(side="left",padx=8)
        ttk.Button(buttons,text="全部会话…",command=self.all).pack(side="left")
        ttk.Button(buttons,text="恢复队列…",command=lambda:resume(app)).pack(side="right")

    def selected(self):
        ids = [self.infos[int(i)].chat_id for i in self.tree.selection()]
        self.choose(ids)

    def _incremental_mode(self):
        self.rebuild_button.state(["!disabled"] if self.incremental.get() else ["disabled"])
        if not self.incremental.get():
            self.rebuild.set(False)

    def all(self):
        if messagebox.askyesno("全部会话","将导出当前账号全部会话，是否继续？",parent=self):
            self.choose([i.chat_id for i in self.infos])

    def choose(self,ids):
        if not ids:
            messagebox.showinfo("未选择会话","请至少选择一个会话。",parent=self)
            return
        session = self.session
        def chosen(formats,out):
            if session.retired:
                messagebox.showinfo("数据源已更新","请重新打开批量导出以使用当前会话列表。",parent=self)
                return
            if self.rebuild.get() and not messagebox.askyesno("全量重建","将完整替换选中会话的已有导出，原 QQ 数据不变。是否继续？",parent=self):
                return
            job = ExportJob(session.source.account_uin,ids,
                orchestrator.ExportOptions(formats=formats,out_base=out,incremental=self.incremental.get(),rebuild=self.rebuild.get()),write_manifest=self.manifest.get())
            if self.remember.get():
                job.checkpoint = config.app_dir()/"jobs"/(job.id+".json")
            self.app.last_out = str(out)
            JobDialog(self.app,session,job)
            self.destroy()
        FormatDialog(self,self.app.last_out,on_ok=chosen)


class JobDialog(tk.Toplevel):
    def __init__(self,app,session,job):
        super().__init__(app.root)
        self.app,self.session,self.job = app,session,job
        self.events,self.future = queue.Queue(),None
        self.title("导出队列")
        self.geometry("720x500")
        self.tree = ttk.Treeview(self,columns=("chat","status","messages","error"),show="headings")
        for name,label,width in (("chat","会话",300),("status","状态",100),("messages","消息数",80),("error","原因",200)):
            self.tree.heading(name,text=label)
            self.tree.column(name,width=width)
        self.tree.pack(fill="both",expand=True,padx=10,pady=10)
        self.names = {info.chat_id:info.title for info in app.infos}
        for i,task in enumerate(job.tasks):
            self.tree.insert("","end",iid=str(i))
        buttons = ttk.Frame(self,padding=10)
        buttons.pack(fill="x")
        ttk.Button(buttons,text="取消",command=job.cancel.set).pack(side="left")
        ttk.Button(buttons,text="重试失败/继续",command=self.retry).pack(side="left",padx=6)
        self.status = ttk.Label(buttons)
        self.status.pack(side="left",padx=6)
        self.protocol("WM_DELETE_WINDOW",self.close)
        app._jobs.append(job)
        self.start()
        self.timer = self.after(80,self.poll)

    def start(self):
        if self.future is not None and not self.future.done():
            return
        self.future = self.app._submit(self.session,lambda repo:self.job.run(repo,progress=self.events.put),
                                      lambda _:None,error=lambda exc:self.events.put({"error":str(exc) if isinstance(exc,JobError) else type(exc).__name__}))

    def retry(self):
        if self.future is not None and not self.future.done():
            return
        if self.session.retired:
            current = self.app.session
            if current is None or current.source is None or account_hash(current.source.account_uin)!=self.job.account:
                messagebox.showinfo("账号已切换","请切回原账号再继续，或保留队列稍后恢复。",parent=self)
                return
            self.session = current
        self.job.retry_failed()
        self.start()

    def poll(self):
        if self.future is not None and self.future.done() and self.job in self.app._jobs:
            self.app._jobs.remove(self.job)
        latest = None
        while not self.events.empty():
            latest = self.events.get_nowait()
        if latest and "error" in latest:
            self.status.configure(text=latest["error"])
        elif latest:
            labels = {"pending":"等待","running":"正在导出","done":"成功","failed":"失败","cancelled":"已取消","skipped":"已跳过"}
            tasks = latest["tasks"]
            for i,task in enumerate(tasks):
                self.tree.item(str(i),values=(self.names.get(task["chat_id"],task["chat_id"]),
                    labels[task["status"]],str(task["messages"])+(f" (+{task['messages_added']})" if task.get("messages_added") is not None else ""),task["error"] or ""))
            counts = Counter(task["status"] for task in tasks)
            self.status.configure(text=f"总数 {len(tasks)} · 成功 {counts['done']} · 失败 {counts['failed']} · 跳过 {counts['skipped']} · 等待 {counts['pending']}")
        self.timer = self.after(80,self.poll)

    def close(self):
        if self.future is not None and not self.future.done():
            self.job.cancel.set()
        elif self.job in self.app._jobs:
            self.app._jobs.remove(self.job)
        self.after_cancel(self.timer)
        self.destroy()


def resume(app):
    if app.session is None:
        return
    path = filedialog.askopenfilename(parent=app.root,title="恢复本机导出队列",
        initialdir=str(config.app_dir()/"jobs"),filetypes=[("导出队列","*.json")])
    if not path:
        return
    try:
        job = ExportJob.load(Path(path),app.session.source.account_uin)
        if messagebox.askyesno("恢复队列","继续未完成会话，并保留校验通过的已完成产物？",parent=app.root):
            JobDialog(app,app.session,job)
    except JobError as exc:
        messagebox.showerror("无法恢复",str(exc),parent=app.root)
