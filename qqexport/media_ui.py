"""缺失媒体定位与显式缓存清理。"""
from datetime import datetime
import tkinter as tk
from tkinter import ttk,messagebox
from qqexport.export.cancel import check_cancel
from qqexport.archive_ui import ArchiveTask


def missing_media(repo,chat,cancel=None):
    # 清单按页显示；生成器不保留全部正文或媒体路径。
    for index,message in enumerate(repo.iter_messages(chat)):
        check_cancel(cancel)
        for segment in message.segments:
            media = segment.media
            if media is not None and (media.missing or not media.data_uri and (not media.source or not media.source.is_file())):
                yield index,message.msg_id,message.ts,media.kind


def show_missing(app):
    if app.session is None or not app.current_chat_id:
        return
    session,chat = app.session,app.current_chat_id
    dialog = tk.Toplevel(app.root)
    title = next((info.title for info in app.infos if info.chat_id==chat),chat)
    dialog.title("本机缺失媒体 · "+title)
    ttk.Label(dialog,text="请先在 QQ 打开对应聊天并下载附件，再点“重新检测”。本工具仅检测本机缓存。",wraplength=650).pack(padx=12,pady=10)
    tree = ttk.Treeview(dialog,columns=("id","time","kind"),show="headings",height=15)
    for name,title in [("id","消息 ID"),("time","时间"),("kind","类型")]:
        tree.heading(name,text=title)
    tree.pack(fill="both",expand=True,padx=12)
    status = ttk.Label(dialog,text="正在检测…")
    status.pack()
    offset,total,positions = 0,0,{}
    def scan():
        nonlocal offset,total
        if session.retired:
            status.configure(text="资料已切换，请关闭后重新检测")
            return
        start = offset
        def work(repo):
            from itertools import islice
            return list(islice(missing_media(repo,chat),start,start+200))
        def ready(rows):
            nonlocal offset,total
            if not dialog.winfo_exists():
                return
            tree.delete(*tree.get_children())
            positions.clear()
            for i,(position,mid,ts,kind) in enumerate(rows):
                tree.insert("","end",iid=str(i),values=(mid,datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S"),kind))
                positions[str(i)] = position
            total = len(rows)
            status.configure(text=f"缺失项 {offset+1}–{offset+len(rows)}（每页最多 200）" if rows else "本页没有缺失项")
        app._submit(session,work,ready)
    def jump(_):
        selected = tree.selection()
        if selected and session is app.session and chat==app.current_chat_id:
            app.virtual.see(positions[selected[0]])
    tree.bind("<Double-1>",jump)
    def page(delta):
        nonlocal offset
        if delta<0 or total==200:
            offset = max(0,offset+delta*200)
            scan()
    bar = ttk.Frame(dialog)
    bar.pack(pady=8)
    ttk.Button(bar,text="上一页",command=lambda:page(-1)).pack(side="left")
    ttk.Button(bar,text="下一页",command=lambda:page(1)).pack(side="left")
    def retry():
        if app.source and app.source.meta.get("account"):
            dialog.destroy()
            app._open_account(app.source.meta["account"],refresh=True)
        else:
            scan()
    ttk.Button(bar,text="重新检测",command=retry).pack(side="left")
    scan()


def clear_thumbnails(app):
    if messagebox.askyesno("清理图片缓存","删除缩略图和系统查看器的图片副本？正在使用的副本会保留，再次浏览时按需生成。",parent=app.root):
        from qqexport.thumbnail_cache import ThumbnailCache
        from qqexport.viewer_images import clear
        def work(_,cancel):
            ThumbnailCache().clear()
            return clear()
        def done(retained):
            app._thumbs.clear()
            app.status.configure(text="图片缓存已清理"+("，正在使用的副本已保留" if retained else ""))
        ArchiveTask(app,"清理图片缓存",work,done)
