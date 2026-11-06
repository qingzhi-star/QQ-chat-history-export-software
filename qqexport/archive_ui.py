"""归档导出与离线打开；密码只在本次操作内使用。"""
from __future__ import annotations
import os
from pathlib import Path
import threading
import tkinter as tk
from tkinter import ttk,filedialog,messagebox,simpledialog

from qqexport import __version__
from qqexport.errors import UserError
from qqexport.export.archive import ArchiveReader,create_archive,encrypted
from qqexport.export.cancel import ExportCancelled
from qqexport.export.serde import copy_file


class ArchiveTask(tk.Toplevel):
    def __init__(self,app,title,work,done,session=None):
        super().__init__(app.root)
        self.app,self.cancel = app,threading.Event()
        self.title(title)
        self.transient(app.root)
        ttk.Label(self,text="正在处理本机归档…",padding=18).pack()
        self.status = ttk.Label(self,text="原始 QQ 数据不变")
        self.status.pack(padx=18,pady=6)
        ttk.Button(self,text="取消",command=self.cancel.set).pack(pady=12)
        self.protocol("WM_DELETE_WINDOW",self.close)
        def ready(value):
            if not self.winfo_exists():
                if hasattr(value,"retire"):
                    app._defer_cleanup(value.retire)
                return
            done(value)
            self.destroy()
        def error(exc):
            if self.winfo_exists():
                self.status.configure(text="已取消" if isinstance(exc,ExportCancelled) else
                                      str(exc) if isinstance(exc,UserError) else type(exc).__name__)
        if session is not None:
            self.future = app._submit(session,lambda repo:work(repo,self.cancel),ready,error=error)
        else:
            self.future = app.tasks.submit(lambda:work(None,self.cancel),ready,error)

    def close(self):
        self.cancel.set()
        self.destroy()


def export_archive(app):
    session = app.session
    ids = list(app.batch_selected) or ([app.current_chat_id] if app.current_chat_id else [])
    if not ids:
        messagebox.showinfo("请选择会话","请先选中会话，或 Ctrl 多选会话后导出归档。",parent=app.root)
        return
    output = filedialog.asksaveasfilename(parent=app.root,title="保存归档",initialdir=app.last_out,
        initialfile=f"QQExport-{__version__}.qqarchive",defaultextension=".qqarchive",filetypes=[("QQ 归档","*.qqarchive")])
    if not output:
        return
    password = None
    if messagebox.askyesno("归档密码","是否使用密码加密归档？密码仅用于本次导出，不会保存，遗忘后无法恢复。",parent=app.root):
        password = simpledialog.askstring("归档密码","密码至少 8 个字符：",show="*",parent=app.root)
        if password is None:
            return
        confirm = simpledialog.askstring("确认密码","再次输入密码：",show="*",parent=app.root)
        if confirm!=password or not 8<=len(password)<=4096:
            messagebox.showerror("密码不符","两次密码须一致，长度为 8–4096 字符。",parent=app.root)
            return
    if session.retired:
        messagebox.showinfo("资料已更新","请重新选择当前资料中的会话后导出。",parent=app.root)
        return
    ArchiveTask(app,"创建归档",lambda repo,cancel:create_archive(repo,ids,Path(output),password=password,
        cancel=cancel,overwrite=True),lambda result:app.status.configure(text=f"归档完成：{result['chats']} 个会话，{result['messages']} 条消息"),session)


def open_archive(app):
    path = filedialog.askopenfilename(parent=app.root,title="打开离线归档",filetypes=[("QQ 归档","*.qqarchive")])
    if not path:
        return
    def checked(needs_password):
        password = None
        if needs_password:
            password = simpledialog.askstring("归档密码","输入密码（不会保存）：",show="*",parent=app.root)
            if password is None:
                return
        ArchiveTask(app,"校验归档",lambda _,cancel:ArchiveReader(path,password=password,cancel=cancel),app.mount_archive)
    app.tasks.submit(lambda:encrypted(path),checked,app._error)


def import_library(app):
    if not messagebox.askyesno("本机明文资料库","导入后会在本机长期保存消息及附件的明文副本，即使原归档已加密。可右击导出按钮，在更多功能中清空。继续？",parent=app.root):
        return
    path = filedialog.askopenfilename(parent=app.root,title="导入归档",filetypes=[("QQ 归档","*.qqarchive")])
    if not path:
        return
    def checked(needs_password):
        password = simpledialog.askstring("归档密码","输入密码（不会保存）：",show="*",parent=app.root) if needs_password else None
        if needs_password and password is None:
            return
        def work(_,cancel):
            from qqexport.library import Library
            reader = ArchiveReader(path,password=password,cancel=cancel)
            library = None
            try:
                library = Library()
                result = library.import_archive(reader,cancel=cancel)
                return library.source(result["owner"])
            finally:
                reader.close()
                if library:
                    library.close()
        ArchiveTask(app,"合并资料库",work,lambda source:app.mount_offline(source,"资料库已合并"))
    app.tasks.submit(lambda:encrypted(path),checked,app._error)


def open_library(app):
    from qqexport.library import Library
    def accounts():
        library = Library()
        try:
            return library.accounts()
        finally:
            library.close()
    def choose(rows):
        if not rows:
            messagebox.showinfo("资料库为空","请先导入归档。",parent=app.root)
            return
        def load(owner):
            def work(_,cancel):
                library = Library()
                try:
                    return library.source(owner)
                finally:
                    library.close()
            ArchiveTask(app,"打开资料库",work,lambda source:app.mount_offline(source,"本机资料库"))
        if len(rows)==1:
            load(rows[0]["owner"])
            return
        dialog = tk.Toplevel(app.root)
        dialog.title("选择资料库账号")
        for row in rows:
            def selected(owner=row["owner"]):
                dialog.destroy()
                load(owner)
            ttk.Button(dialog,text=f"账号 {row['owner'][:12]} · {row['messages']} 条消息",command=selected).pack(padx=16,pady=6)
    app.tasks.submit(accounts,choose,app._error)


def clear_library(app):
    from qqexport.library import Library
    if not messagebox.askyesno("清空本机资料库","删除资料库内全部账号的明文消息及附件？归档文件保留。若资料库正在浏览，请先打开其他资料。",parent=app.root):
        return
    ArchiveTask(app,"清空资料库",lambda _,cancel:Library.clear(),lambda _:app.status.configure(text="本机资料库已清空"))


def open_attachment(app,media):
    if media is None:
        return
    session = app.session
    if media.kind=="image":
        from qqexport.viewer_images import prepare
        def ready(path):
            try:
                os.startfile(path)
            except OSError:
                messagebox.showerror("无法打开图片","请在 Windows 中设置默认图片查看器后重试。",parent=app.root)
        app._submit(session,lambda _:prepare(media),ready)
        return
    if not media.source or not media.source.is_file():
        messagebox.showinfo("附件不可用","本机没有此附件，请在 QQ 中打开对应聊天后重新检测。",parent=app.root)
        return
    output = filedialog.asksaveasfilename(parent=app.root,title="保存附件后打开",initialdir=app.last_out,
                                         initialfile=media.filename or media.source.name)
    if output:
        if session is None or session.retired:
            return
        from qqexport.export.orchestrator import validate_destination
        try:
            validate_destination(session.source,Path(output))
        except ValueError as exc:
            messagebox.showerror("无法保存",str(exc),parent=app.root)
            return
        def copy(_,cancel):
            import time
            destination = Path(output)
            temporary = destination.with_name(f".attachment-{os.getpid()}-{time.time_ns()}.tmp")
            try:
                copy_file(media.source,temporary,cancel)
                from qqexport.export.cancel import check_cancel
                check_cancel(cancel)
                os.replace(temporary,destination)
                return output
            finally:
                temporary.unlink(missing_ok=True)
        ArchiveTask(app,"保存附件",copy,lambda path:os.startfile(path),session)
