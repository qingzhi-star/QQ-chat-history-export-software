"""P2 主界面：沿用双栏视觉，分页虚拟化与主线程结果调度。"""
from __future__ import annotations

import base64
from collections import OrderedDict
import io
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

from qqexport import config, selection
from qqexport.core import discover, keystore
from qqexport.export import orchestrator
from qqexport.export.session import ExportSource
from qqexport.gui import App, FormatDialog
from qqexport.repository import MessageRepository
from qqexport.tasks import DataSession, TaskRunner
from qqexport.virtual_list import VirtualMessages


class ProductApp(App):
    def __init__(self, root, **kwargs):
        self.tasks = TaskRunner(root)
        self.session = None
        self._source_gen = 0
        self._closed = False
        self._thumbs = OrderedDict()
        self._image_pending = set()
        self._jobs,self.batch_selected = [],set()
        super().__init__(root, **kwargs)
        root.bind("<Escape>", lambda _: self._cancel_export() if self.export_mode else None)

    def _build(self):
        super()._build()
        self.text.pack_forget()
        self.tsb.pack_forget()
        self.virtual = VirtualMessages(self.text.master, self._request_page, self._toggle_check,
                                       on_open=self._view_media, image_loader=self._load_image)
        self.virtual.pack(fill="both", expand=True)
        self.tools_menu = tk.Menu(self.root,tearoff=False)
        self.tools_menu.add_command(label="搜索消息",command=self._show_search)
        self.tools_menu.add_command(label="批量导出…",command=self._show_batch)
        self.tools_menu.add_command(label="恢复导出队列…",command=self._resume_queue)
        self.tools_menu.add_command(label="导出归档…",command=self._archive_export)
        self.tools_menu.add_command(label="打开/校验归档…",command=self._archive_open)
        self.tools_menu.add_command(label="本机缺失媒体…",command=self._missing_media)
        self.tools_menu.add_command(label="清理缩略图缓存…",command=self._clear_thumbnails)
        for label,action in [("归档导入资料库…","import_library"),("打开本机资料库…","open_library"),("清空本机资料库…","clear_library")]:
            self.tools_menu.add_command(label=label,command=lambda action=action:self._library_action(action))
        menu = tk.Menu(self.root)
        menu.add_cascade(label="工具",menu=self.tools_menu)
        self.root.configure(menu=menu)

    def _archive_export(self):
        if self.session is not None:
            from qqexport.archive_ui import export_archive
            export_archive(self)

    def _archive_open(self):
        from qqexport.archive_ui import open_archive
        open_archive(self)

    def _missing_media(self):
        from qqexport.media_ui import show_missing
        show_missing(self)

    def _clear_thumbnails(self):
        from qqexport.media_ui import clear_thumbnails
        clear_thumbnails(self)

    def mount_archive(self,reader):
        self.mount_offline(reader.source(),"离线归档已校验")

    def _library_action(self,action):
        from qqexport import archive_ui
        getattr(archive_ui,action)(self)

    def mount_offline(self,source,label):
        from qqexport.export.archive import ArchiveRepository
        old = self.session
        self._source_gen += 1
        self._render_gen += 1
        if self._watch_id:
            self.root.after_cancel(self._watch_id)
            self._watch_id = None
        self.session = DataSession(source,ArchiveRepository(source),cleanup=self._defer_cleanup)
        self.batch_selected.clear()
        self._thumbs.clear()
        self.current_chat_id = None
        self.uin = None
        self._loaded(source,source.chats(),{}, {})
        self.virtual.reset(0)
        if old:
            old.retire()
        self.status.configure(text=label+"；选择会话浏览，可搜索或导出，不需要 QQ")

    def _view_media(self,media):
        if self.source and self.source.meta.get("archive"):
            from qqexport.archive_ui import open_attachment
            open_attachment(self,media)
        else:
            super()._view_media(media)

    def _defer_cleanup(self,work):
        if self.tasks.closed:
            work()
        else:
            try:
                self.tasks.submit(work,lambda _:None,self._error)
            except RuntimeError:
                work()

    def _show_batch(self):
        if self.session is not None:
            from qqexport.queue_ui import BatchDialog
            BatchDialog(self)

    def _resume_queue(self):
        from qqexport.queue_ui import resume
        resume(self)

    def _make_row(self,idx,info):
        super()._make_row(idx,info)
        for widget in self.row_widgets[-1]["widgets"]:
            widget.bind("<Control-Button-1>",lambda _,i=idx:self._batch_toggle(i))

    def _batch_toggle(self,idx):
        chat = self.infos[idx].chat_id
        if chat in self.batch_selected:
            self.batch_selected.remove(chat)
        else:
            self.batch_selected.add(chat)
        self._paint_rows()
        self.status.configure(text=f"已选择 {len(self.batch_selected)} 个会话，可从工具菜单批量导出")
        return "break"

    def _paint_rows(self):
        super()._paint_rows()
        for i,row in enumerate(self.row_widgets):
            if self.infos[i].chat_id in self.batch_selected:
                for widget in row["widgets"]:
                    widget.configure(bg="#deebfb")

    def _show_search(self):
        if self.session is not None:
            from qqexport.search_ui import SearchDialog
            existing = getattr(self,"_search_dialog",None)
            if existing is not None and existing.winfo_exists() and existing.session is self.session:
                existing.lift()
            else:
                if existing is not None and existing.winfo_exists():
                    existing.close()
                self._search_dialog = SearchDialog(self)

    def jump_to_hit(self, hit):
        idx = next((i for i,info in enumerate(self.infos) if info.chat_id==hit.chat_id),None)
        if idx is None:
            return
        self.selected_idx = idx
        self._paint_rows()
        self._pending_jump = hit.position
        self._start_preview(self.infos[idx],anchor=(max(0,hit.position-1),0),bottom=False)

    def _error(self, exc):
        self._log_exception("product", exc)
        self._busy, self._refreshing = False, False
        self.virtual.pending.clear()
        self.status.configure(text=f"操作失败：{type(exc).__name__}", foreground="#b00")

    def _submit(self, session, work, done, *, gen=None, error=None):
        if self._closed:
            return
        try:
            release = session.pin()
        except RuntimeError as exc:
            self._error(exc)
            return
        source_gen = self._source_gen
        def valid():
            return (not self._closed and source_gen == self._source_gen
                    and (gen is None or gen == self._render_gen))
        future = self.tasks.submit(lambda: work(session.repository),
                                  lambda value: done(value) if valid() else None,
                                  lambda exc: (error or self._error)(exc) if valid() else None)
        future.add_done_callback(lambda _: release())
        return future

    def _load_chats(self):
        self._source_gen += 1
        gen = self._source_gen
        self.export_btn.state(["disabled"])
        def ready(accounts):
            if gen != self._source_gen:
                return
            if self.uin:
                self._open_account(discover.select_account(accounts,self.uin))
            elif len(accounts) == 1:
                self._open_account(accounts[0])
            elif accounts:
                self._choose_account(accounts)
            else:
                self.status.configure(text="未找到已登录账号，请检查 QQ 或手动指定数据目录")
        def discover_accounts():
            from qqexport.temporary import cleanup_stale
            cleanup_stale()
            return discover.discover_accounts(explicit=self.data_dir)
        self.tasks.submit(discover_accounts, ready, self._error)

    def _choose_account(self, accounts):
        dialog = tk.Toplevel(self.root)
        dialog.title("选择本机账号")
        dialog.transient(self.root)
        ttk.Label(dialog,text="请选择本人账号（不会默认使用第一个）：").pack(padx=16,pady=10)
        choice = ttk.Combobox(dialog,values=[discover.mask_uin(a.uin) for a in accounts],state="readonly")
        choice.pack(padx=16,pady=8)
        def accept():
            if choice.current() < 0:
                return
            account = accounts[choice.current()]
            dialog.destroy()
            self._open_account(account)
        ttk.Button(dialog,text="确定",command=accept).pack(pady=12)
        dialog.grab_set()

    def _open_account(self, account, *, refresh=False):
        self._source_gen += 1
        gen = self._source_gen
        self.status.configure(text="正在读取账号…")
        anchor = self.virtual.anchor()
        bottom = self.virtual.at_bottom()
        current_id = self.current_chat_id if refresh else None
        def work():
            key = keystore.KeyStore().load(account.uin)
            if not key:
                raise RuntimeError("没有缓存口令，请先运行 key set 或 key scan")
            source = ExportSource.open(account,key)
            try:
                repo = MessageRepository(source)
                session = DataSession(source,repo,cleanup=self._defer_cleanup)
                session.infos = source.chats()
                session.metas = {}
                for info in session.infos:
                    last = repo.page(info.chat_id,limit=1,reverse=True).messages
                    if last:
                        from qqexport.export.txt import segment_text
                        session.metas[info.chat_id] = (" ".join(segment_text(s) for s in last[-1].segments)[:48],last[-1].ts)
                return session
            except BaseException:
                source.close()
                raise
        def ready(session):
            if gen != self._source_gen:
                session.retire()
                return
            old = self.session
            if not refresh:
                self.batch_selected.clear()
            self.session = session
            self._thumbs.clear()
            self.uin = account.uin
            self._refreshing = False
            self._loaded(session.source,session.infos,session.metas,{})
            if old:
                old.retire()
            selected = next((i for i,x in enumerate(self.infos) if x.chat_id == current_id),None)
            if selected is not None:
                self.selected_idx = selected
                self._paint_rows()
                self._start_preview(self.infos[selected],anchor=anchor,bottom=bottom)
            elif not refresh:
                self.current_chat_id = None
                self.virtual.reset(0)
                self.status.configure(text="选择会话；双击打开本机附件，右键复制消息")
        self.tasks.submit(work,ready,self._error)

    def _select_chat(self, idx):
        if self.source is None or idx >= len(self.infos):
            return
        self.selected_idx = idx
        self._paint_rows()
        info = self.infos[idx]
        if info.chat_id != self.current_chat_id:
            self._start_preview(info)

    def _start_preview(self, info, *, anchor=None, bottom=True):
        self._render_gen += 1
        gen = self._render_gen
        self.current_chat_id = info.chat_id
        self.title_label.configure(text=info.title)
        self.selected_msgs.clear()
        self._exit_export_mode()
        self._busy = True
        self.export_btn.state(["disabled"])
        session = self.session
        def ready(count):
            self.preview = []
            self.virtual.reset(count,anchor=anchor,bottom=bottom)
            if getattr(self,"_pending_jump",None) is not None:
                self.virtual.highlight = self._pending_jump
                self._pending_jump = None
            self._busy = False
            self.export_btn.state(["!disabled"] if count else ["disabled"])
            self.status.configure(text=f"共 {count} 条 · 按需加载")
        self._submit(session,lambda repo: repo.count(info.chat_id),ready,gen=gen)

    def _request_page(self, offset, size):
        if self.session is None or self.current_chat_id is None:
            return
        chat = self.current_chat_id
        self._submit(self.session,lambda repo: repo.window(chat,offset,size),
                     lambda page: self.virtual.loaded(offset,page),gen=self._render_gen)

    def _load_image(self, index, media):
        gen = self._render_gen
        key = str(media.source) if media.source else media.data_uri
        if not key:
            return
        if key in self._thumbs:
            self.virtual.after_idle(lambda: self.virtual.image_ready(index,self._thumbs.get(key)) if gen == self._render_gen else None)
            return
        if (gen,index) in self._image_pending:
            return
        self._image_pending.add((gen,index))
        try:
            release = self.session.pin() if self.session is not None else lambda:None
        except RuntimeError:
            return
        def decode():
            from qqexport.thumbnail_cache import ThumbnailCache
            try:
                return ThumbnailCache(getattr(self,"thumbnail_limit",2*1024**3)).load(media)
            except (OSError,ValueError):
                return None
        def ready(image):
            self._image_pending.discard((gen,index))
            if gen != self._render_gen:
                return
            if image is not None:
                self._thumbs[key] = image
                self._thumbs.move_to_end(key)
                while len(self._thumbs)>64:
                    self._thumbs.popitem(last=False)
                if gen == self._render_gen:
                    self.virtual.image_ready(index,image)
        future = self.tasks.submit(decode,ready,self._error)
        future.add_done_callback(lambda _:release())

    def _open_export(self):
        if self.selected_idx is None or not self.virtual.count:
            return
        self.export_mode = True
        self.selected_msgs.clear()
        self.date_start = self.date_end = None
        self.export_center.pack(side="right")
        self.export_right.pack(side="right")
        self.virtual.selection(True,self.selected_msgs)
        self._update_all_label()

    def _exit_export_mode(self):
        self.export_mode = False
        self.export_center.pack_forget()
        self.export_right.pack_forget()
        self.date_label.pack_forget()
        if hasattr(self,"virtual"):
            self.virtual.selection(False,self.selected_msgs)

    def _toggle_check(self, index):
        selection.toggle(self.selected_msgs,index)
        self.virtual.selection(True,self.selected_msgs)
        self._update_all_label()

    def _toggle_all(self):
        self._clear_date_filter()
        if len(self.selected_msgs) == self.virtual.count:
            self.selected_msgs.clear()
        else:
            self.selected_msgs = set(range(self.virtual.count))
        self.virtual.selection(True,self.selected_msgs)
        self._update_all_label()

    def _update_all_label(self):
        self.all_btn.configure(text="全不选" if len(self.selected_msgs)==self.virtual.count else "全选")
        self.status.configure(text=f"已选择 {len(self.selected_msgs)} / {self.virtual.count} 条")

    def _apply_dates(self, start, end):
        self.date_start,self.date_end = start,end
        chat = self.current_chat_id
        def ready(indices):
            self.selected_msgs = indices
            self.virtual.selection(True,indices)
            self._update_all_label()
        self._submit(self.session,lambda repo: repo.indices_in_range(chat,start,end),ready,gen=self._render_gen)

    def _next_step(self):
        if not self.selected_msgs:
            messagebox.showinfo("未选择消息","请至少选择一条消息。")
            return
        info = self.infos[self.selected_idx]
        session = self.session
        selected = set(self.selected_msgs)
        start,end = self.date_start,self.date_end
        def ready(ids):
            FormatDialog(self.root,self.last_out,on_ok=lambda formats,out:
                         self._export_selected(session,info,ids,start,end,formats,out))
        self._submit(session,lambda repo: repo.ids_for_indices(info.chat_id,selected),ready,gen=self._render_gen)

    def _export_selected(self, session, info, ids, start, end, formats, output):
        from qqexport.export.jobs import ExportJob
        from qqexport.queue_ui import JobDialog
        self.last_out = str(output)
        if session.retired:
            messagebox.showinfo("数据源已更新","请重新选择消息后导出。")
            return
        job = ExportJob(session.source.account_uin,[info.chat_id],
            orchestrator.ExportOptions(formats=formats,out_base=output,start_ts=start,end_ts=end,message_ids=ids))
        JobDialog(self,session,job)

    def _watch_live(self):
        self._watch_id = None
        if self.session is None or self._closed:
            return
        stamp = self._current_mtime()
        if stamp and stamp != self._wal_mtime and not self._busy and not self.export_mode and not self._refreshing:
            self._refreshing = True
            self._open_account(self.source.meta["account"],refresh=True)
        self._schedule_watch()

    def close(self):
        if self._closed:
            return
        self._closed = True
        self.root.withdraw()
        for job in self._jobs:
            job.cancel.set()
        for child in self.root.winfo_children():
            if isinstance(child,tk.Toplevel):
                closer = getattr(child,"close",None)
                if callable(closer):
                    closer()
                else:
                    child.destroy()
            cancel = getattr(child,"cancel",None)
            if hasattr(cancel,"set"):
                cancel.set()
        if self._watch_id:
            self.root.after_cancel(self._watch_id)
        self.tasks.close()
        if self.session:
            self.session.retire()
        self._finish_close()

    def _finish_close(self):
        if not self.tasks.idle():
            self.root.after(25,self._finish_close)
            return
        self.tasks.pool.shutdown(wait=True)
        self._thumbs.clear()
        self.virtual.pages.clear()
        self.root.destroy()
