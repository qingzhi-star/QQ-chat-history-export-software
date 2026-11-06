"""分页搜索窗口、精确结果跳转与结果集导出。"""
from __future__ import annotations
import json
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime

from qqexport import config, selection
from qqexport.export import orchestrator
from qqexport.gui import FormatDialog
from qqexport.search import SearchIndex, Query, ResultSource


class SearchDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root)
        self.app,self.session,self.offset,self.hits = app,app.session,0,[]
        self.cancel = threading.Event()
        self.generation = 0
        self.title("本机消息搜索")
        self.geometry("1020x650")
        self.transient(app.root)
        top = ttk.Frame(self,padding=10)
        top.pack(fill="x")
        self.text = ttk.Combobox(top,width=40)
        self.text.grid(row=0,column=0,columnspan=2,padx=4,pady=4)
        self.scope = ttk.Combobox(top,values=["当前会话","全部会话"],state="readonly",width=12)
        self.scope.set("当前会话" if app.current_chat_id else "全部会话")
        self.scope.grid(row=0,column=2,padx=4)
        self.mode = ttk.Combobox(top,values=["contains","exact","regex"],state="readonly",width=10)
        self.mode.set("contains")
        self.mode.grid(row=0,column=3,padx=4)
        ttk.Button(top,text="搜索",command=lambda:self.search(0)).grid(row=0,column=4,padx=4)
        self.sender,self.start,self.end = (ttk.Entry(top,width=18) for _ in range(3))
        for column,(label,widget) in enumerate(zip(("发送者（显示名/UID/QQ号）","开始 YYYY-MM-DD","结束 YYYY-MM-DD"),
                                                  (self.sender,self.start,self.end))):
            ttk.Label(top,text=label).grid(row=1,column=column,pady=(8,0))
            widget.grid(row=2,column=column,padx=4,pady=4)
        self.kind = ttk.Combobox(top,values=["全部","text","image","file","voice","reply","recall","forward_nt","forward_legacy","ark"],state="readonly",width=12)
        self.kind.set("全部")
        self.kind.grid(row=2,column=3,padx=4)
        self.history = tk.BooleanVar(value=False)
        ttk.Checkbutton(top,text="保存搜索历史（仅本机）",variable=self.history).grid(row=3,column=0,columnspan=2,sticky="w")
        ttk.Button(top,text="清空历史",command=self.clear_history).grid(row=3,column=2)
        ttk.Button(top,text="清除内存索引",command=self.clear_index).grid(row=3,column=3)
        ttk.Label(top,text="exact 匹配整条正文；regex 为限时高级正则；索引不写入磁盘。",foreground="#666").grid(row=4,column=0,columnspan=5,sticky="w",pady=5)
        self.tree = ttk.Treeview(self,columns=("chat","sender","time","summary"),show="headings",height=18)
        for name,label,width in (("chat","会话",170),("sender","发送者",180),("time","时间",150),("summary","摘要",460)):
            self.tree.heading(name,text=label)
            self.tree.column(name,width=width)
        self.tree.pack(fill="both",expand=True,padx=10)
        self.tree.bind("<Double-1>",self.jump)
        bottom = ttk.Frame(self,padding=10)
        bottom.pack(fill="x")
        ttk.Button(bottom,text="上一页",command=lambda:self.search(max(0,self.offset-200))).pack(side="left")
        ttk.Button(bottom,text="下一页",command=lambda:self.search(self.offset+200)).pack(side="left",padx=5)
        ttk.Button(bottom,text="导出全部匹配结果",command=self.export).pack(side="left",padx=8)
        ttk.Button(bottom,text="取消索引",command=self.cancel.set).pack(side="right")
        self.status = ttk.Label(bottom,text="输入关键词或组合筛选")
        self.status.pack(side="left",padx=8)
        self.protocol("WM_DELETE_WINDOW",self.close)
        self.query = None
        path = config.app_dir()/"search-history.json"
        try:
            values = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(values,list):
                self.text.configure(values=[v for v in values if isinstance(v,str)][:30])
        except (ValueError,OSError):
            pass

    def valid(self):
        return self.winfo_exists() and self.session is self.app.session

    def _index(self, repo):
        with self.session.lock:
            if not hasattr(self.session,"search"):
                self.session.search = SearchIndex()
            index = self.session.search
        with index.lock:
            if not index.ready:
                index.build(repo,cancel=self.cancel)
        return index

    def search(self, offset):
        if not self.valid():
            return
        try:
            query = Query(self.text.get(),self.app.current_chat_id if self.scope.get()=="当前会话" else None,
                self.sender.get().strip() or None,None if self.kind.get()=="全部" else self.kind.get(),
                selection.day_timestamp(self.start.get()),selection.day_timestamp(self.end.get(),end=True),self.mode.get())
        except ValueError as exc:
            self.status.configure(text=str(exc))
            return
        self.cancel.clear()
        self.generation += 1
        generation = self.generation
        self.status.configure(text="建立/查询内存索引…")
        def ready(hits):
            if not self.valid() or generation != self.generation:
                return
            self.query,self.offset,self.hits = query,offset,hits
            self.tree.delete(*self.tree.get_children())
            titles = {i.chat_id:i.title for i in self.app.infos}
            for i,hit in enumerate(hits):
                self.tree.insert("","end",iid=str(i),values=(titles.get(hit.chat_id,hit.chat_id),hit.sender,
                    datetime.fromtimestamp(hit.ts).strftime("%Y-%m-%d %H:%M:%S"),hit.summary.replace("\n"," ")))
            self.status.configure(text=f"显示 {offset+1}–{offset+len(hits)} 条；双击跳转")
            if self.history.get() and query.text:
                values = [query.text]+[v for v in self.text.cget("values") if v!=query.text]
                self.text.configure(values=values[:30])
                from qqexport.state import atomic_json
                atomic_json(config.app_dir()/"search-history.json",values[:30])
        self.app._submit(self.session,lambda repo:self._index(repo).find(query,offset=offset),ready,
            error=lambda exc:self.status.configure(text=str(exc) if isinstance(exc,ValueError) else type(exc).__name__) if self.winfo_exists() else None)

    def jump(self, _=None):
        selection_ids = self.tree.selection()
        if not selection_ids or not self.valid():
            return
        hit = self.hits[int(selection_ids[0])]
        self.app.jump_to_hit(hit)

    def clear_history(self):
        (config.app_dir()/"search-history.json").unlink(missing_ok=True)
        self.text.configure(values=())
        self.status.configure(text="历史已清除")

    def clear_index(self):
        self.cancel.set()
        self.generation += 1
        self.hits,self.query = [],None
        self.tree.delete(*self.tree.get_children())
        def clear(repo):
            if hasattr(self.session,"search"):
                self.session.search.clear()
        self.app._submit(self.session,clear,lambda _:
            self.status.configure(text="内存索引已清除") if self.winfo_exists() else None)

    def export(self):
        if self.query is None or not self.valid():
            return
        query = self.query
        def chosen(formats,out):
            if formats-{"txt","html"}:
                messagebox.showinfo("搜索结果导出","请选择 TXT 和/或 HTML。",parent=self)
                return
            def work(repo):
                selected = ResultSource(repo,self._index(repo).iter_hits(query))
                return orchestrator.export_chats(selected,list(selected.positions),
                    orchestrator.ExportOptions(formats=formats,out_base=out))
            self.app._submit(self.session,work,lambda report:self.status.configure(text=report.to_text()) if self.winfo_exists() else None)
        FormatDialog(self,self.app.last_out,on_ok=chosen,allowed_formats={"txt","html"})

    def close(self):
        self.cancel.set()
        self.destroy()
