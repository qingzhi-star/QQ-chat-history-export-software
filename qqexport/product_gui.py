"""P2 主界面：沿用双栏视觉，分页虚拟化与主线程结果调度。"""
from __future__ import annotations

import base64
from collections import OrderedDict
import io
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

from qqexport import config, selection
from qqexport.core import discover
from qqexport.export import orchestrator
from qqexport.export.session import ExportSource
from qqexport.gui import App, FormatDialog
from qqexport.repository import MessageRepository
from qqexport.tasks import DataSession, TaskRunner
from qqexport.virtual_list import VirtualMessages
from qqexport.account_access import account_key,AccountAccessError


class ProductApp(App):
    def __init__(self, root, **kwargs):
        self.root = root
        self._config_failed = False
        try:
            self.preferences = config.Config.load()
        except config.ConfigError:
            self.preferences,self._config_failed = config.Config(),True
        self.tasks = TaskRunner(root)
        from qqexport.gui_avatars import GuiAvatars
        self.gui_avatars = GuiAvatars(self)
        self.session = None
        self._source_gen = 0
        self._account_opening = False
        self._closed = False
        self._thumbs = OrderedDict()
        self._sidebar_portraits = OrderedDict()
        self._image_pending = set()
        self._jobs,self.batch_selected = [],set()
        super().__init__(root, **kwargs)
        self.last_out = self.preferences.last_out or self.last_out
        self.thumbnail_limit = self.preferences.thumbnail_mb*1024**2
        self.virtual.density = self.preferences.density
        root.geometry(self.preferences.geometry)
        root.state(self.preferences.window_state)
        if self._config_failed:
            root.after_idle(self._recover_settings)
        root.bind("<Escape>",self._escape)

    def _build(self):
        super()._build()
        from qqexport.chat_sidebar import ChatSidebar
        self.sidebar = ChatSidebar(self)
        from qqexport.account_panel import AccountPanel
        self.count_label.pack_forget()
        self.accounts_panel = AccountPanel(self,self.left_panel,self.chats_label)
        self.account_button = self.accounts_panel.button
        self._build_footer()
        preview_width = self.text.winfo_reqwidth()+self.tsb.winfo_reqwidth()
        preview_height = self.text.winfo_reqheight()
        self.text.pack_forget()
        self.tsb.pack_forget()
        self.virtual = VirtualMessages(self.text.master, self._request_page, self._toggle_check,
                                       on_open=self._view_media, image_loader=self._load_image)
        self.virtual.configure(width=preview_width,height=preview_height)
        self.virtual.pack_propagate(False)
        self.virtual.pack(fill="both", expand=True)
        self.virtual.avatar_uri = self.gui_avatars.message_uri
        self.virtual.avatar_key = self.gui_avatars.message_key
        self.tools_menu = tk.Menu(self.root,tearoff=False)
        self.tools_menu.add_command(label="搜索消息",command=self._show_search)
        self.tools_menu.add_command(label="批量导出…",command=self._show_batch)
        self.tools_menu.add_command(label="恢复导出队列…",command=self._resume_queue)
        self.tools_menu.add_command(label="导出归档…",command=self._archive_export)
        self.tools_menu.add_command(label="打开/校验归档…",command=self._archive_open)
        self.tools_menu.add_command(label="本机缺失媒体…",command=self._missing_media)
        self.tools_menu.add_command(label="清理图片缓存…",command=self._clear_thumbnails)
        self.tools_menu.add_command(label="切换本机账号…",command=self._switch_account)
        self.tools_menu.add_command(label="设置…",command=self._settings)
        self.tools_menu.add_command(label="生成定时导出任务…",command=self._schedule_export)
        for label,action in [("归档导入资料库…","import_library"),("打开本机资料库…","open_library"),("清空本机资料库…","clear_library")]:
            self.tools_menu.add_command(label=label,command=lambda action=action:self._library_action(action))
        # 0.1.0 主窗口不增加菜单栏/按钮；P2 能力保留在右键与快捷键。
        self.virtual.tools_menu = self.tools_menu
        for widget in (self.export_btn,self.title_label,self.canvas,self.virtual.canvas):
            widget.bind("<Button-3>",lambda event:self.tools_menu.tk_popup(event.x_root,event.y_root))
        self.root.bind("<Control-f>",lambda _:self._show_search())
        self.root.bind("<Control-Shift-E>",lambda _:self._show_batch())
        self.virtual.canvas.configure(bd=0,relief="flat",highlightthickness=1,highlightbackground="#b0b0b0")

    def _loaded(self,source,infos,metas,avs):
        infos = sorted(infos,key=lambda info:(-(metas.get(info.chat_id,("",info.last_ts))[1] or 0),info.chat_id))
        same = self.source is not None and self.source.account_uin==source.account_uin
        selected = self.current_chat_id if same else None
        self._preserve_chat_scroll = True
        self._sidebar_same_account = same
        if same:
            avs = self.avatar_data | avs
        self.virtual.source = source
        super()._loaded(source,infos,metas,avs)
        self.selected_idx = next((i for i,info in enumerate(self.infos) if info.chat_id==selected),None)
        self._paint_rows()
        self.gui_avatars.reset(source,infos)
        self.accounts_panel.current(source)

    def _escape(self,_=None):
        if self.accounts_panel.popup is not None:
            self.accounts_panel.close()
        elif self.export_mode:
            self._cancel_export()

    def _build_footer(self):
        from qqexport.ui import ActionButton,INK,MUTED
        bottom = self.export_btn.master
        self.status.pack_forget()
        for widget in (self.export_btn,self.export_center,self.export_right,self.date_label):
            widget.destroy()
        bottom.configure(padding=(8,10))
        self.export_btn = ActionButton(bottom,text="导出记录",primary=True,command=self._open_export)
        self.export_btn.pack(side="left")
        self.export_center = ttk.Frame(bottom)
        self.all_btn = ActionButton(self.export_center,text="全选",command=self._toggle_all)
        self.all_btn.pack(side="left",padx=(0,8))
        self.date_btn = ActionButton(self.export_center,text="时间选择",command=self._pick_dates)
        self.date_btn.pack(side="left")
        self.date_label = ttk.Label(self.export_center,text="",foreground=MUTED,font=("Microsoft YaHei UI",9))
        self.export_right = ttk.Frame(bottom)
        self.cancel_btn = ActionButton(self.export_right,text="取消",command=self._cancel_export)
        self.cancel_btn.pack(side="left",padx=(0,8))
        self.next_btn = ActionButton(self.export_right,text="下一步",primary=True,command=self._next_step)
        self.next_btn.pack(side="left")
        self.status.pack(side="left",padx=14,fill="x",expand=True)

    def _load_chat_rows(self):
        self.sidebar.reset(self._sidebar_same_account)

    def _wheel(self,event):
        return self.sidebar.wheel(event)

    def _sync_chat_scroll(self,event=None):
        if hasattr(self,"sidebar"):
            self.sidebar.schedule()
        else:
            super()._sync_chat_scroll(event)

    def _archive_export(self):
        if self.session is not None:
            from qqexport.archive_ui import export_archive
            export_archive(self)

    def _archive_open(self):
        from qqexport.archive_ui import open_archive
        open_archive(self)

    def _settings(self):
        from qqexport.settings_ui import SettingsDialog
        SettingsDialog(self)

    def _schedule_export(self):
        from qqexport.scheduled import prepare_dialog
        prepare_dialog(self)

    def _recover_settings(self):
        if messagebox.askyesno("配置损坏","配置无法读取。是否先保留原文件备份，再恢复默认设置？取消后本次使用默认设置且不保存。",parent=self.root):
            try:
                config.Config.recover()
                self.preferences,self._config_failed = config.Config(),False
                self.status.configure(text="已备份原配置并恢复默认设置")
            except config.ConfigError as exc:
                self.status.configure(text=str(exc))

    def _save_preferences(self):
        if self._config_failed:
            return
        try:
            self.preferences.save()
        except config.ConfigError as exc:
            self._config_failed = True
            self.status.configure(text=str(exc))

    def remember_export(self,formats,output):
        self.last_out = self.preferences.last_out = str(output)
        self.preferences.formats = sorted(formats)
        self._save_preferences()

    def _switch_account(self):
        if self._account_opening:
            return
        self.tasks.submit(lambda:discover.discover_accounts(explicit=self.data_dir),
            lambda accounts:self._choose_account(accounts) if accounts else self.status.configure(text="未找到本机账号"),self._error)

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
        self._account_opening = False
        self.account_button.state(["!disabled"])
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
        from qqexport.archive_ui import open_attachment
        open_attachment(self,media)

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
        row = self.row_widgets[-1]
        row["index"] = idx
        for widget in row["widgets"]:
            widget.bind("<Button-1>",lambda _,row=row:self._select_chat(row["index"]))
            widget.bind("<Enter>",lambda _,row=row:self._hover_row(row["index"],True))
            widget.bind("<Leave>",lambda _,row=row:self._hover_row(row["index"],False))
            widget.bind("<Control-Button-1>",lambda _,row=row:self._batch_toggle(row["index"]))

    def _row_avatar(self,info):
        data = self.avatar_data.get(info.chat_id)
        key = ("data",data) if data else ("placeholder",info.title)
        photo = self._sidebar_portraits.get(key)
        if photo is None:
            photo = super()._row_avatar(info)
            self._sidebar_portraits[key] = photo
            while len(self._sidebar_portraits)>128:
                self._sidebar_portraits.popitem(last=False)
        else:
            self._sidebar_portraits.move_to_end(key)
            self._row_photos[info.chat_id] = photo
        return photo

    def _batch_toggle(self,idx):
        chat = self.infos[idx].chat_id
        if chat in self.batch_selected:
            self.batch_selected.remove(chat)
        else:
            self.batch_selected.add(chat)
        self._paint_rows()
        self.status.configure(text=f"已选择 {len(self.batch_selected)} 个会话，可右击导出按钮选择批量导出")
        return "break"

    def _paint_rows(self):
        for i,row in enumerate(self.row_widgets):
            if row is None:
                continue
            if self.infos[i].chat_id in self.batch_selected:
                bg = "#deebfb"
            elif i==self.selected_idx:
                bg = "#e6f0ff"
            elif i==getattr(self,"_hover",None):
                bg = "#f3f3f3"
            else:
                bg = "#ffffff"
            if row.get("painted_bg")!=bg:
                for widget in row["widgets"]:
                    widget.configure(bg=bg)
                row["painted_bg"] = bg

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
        if isinstance(exc,AccountAccessError):
            text = exc.user_message
        else:
            text = f"操作失败：{type(exc).__name__}"
        self.status.configure(text=text, foreground="#b00")

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
        self.accounts_panel.show(accounts)

    def _open_account(self, account, *, refresh=False,auto_key=False):
        if self._account_opening:
            return
        if not refresh:
            self._account_opening = True
            self.account_button.state(["disabled"])
        if not refresh:
            self._source_gen += 1
        gen = self._source_gen
        if not refresh:
            self.status.configure(text="正在自动获取口令并读取账号…" if auto_key else "正在读取账号…",foreground="#666")
        previous_media = self.source.media_index if refresh and self.source else None
        def work():
            key = account_key(account,allow_scan=auto_key)
            stamp = self._account_stamp(account)
            source = ExportSource.open(account,key,media_index=previous_media) if previous_media is not None else ExportSource.open(account,key)
            try:
                repo = MessageRepository(source)
                session = DataSession(source,repo,cleanup=self._defer_cleanup)
                session.snapshot_stamp = stamp
                session.infos,session.metas = repo.sidebar()
                return session
            except BaseException:
                source.close()
                raise
        def ready(session):
            if gen != self._source_gen:
                session.retire()
                return
            if refresh and self.export_mode:
                self._refreshing = False
                session.retire()
                return
            self._account_opening = False
            self.account_button.state(["!disabled"])
            self._busy = False
            old = self.session
            if not refresh:
                self.batch_selected.clear()
                self.selected_msgs.clear()
                self._exit_export_mode()
            self.session = session
            self._thumbs.clear()
            self.uin = account.uin
            self.preferences.last_account = account.uin
            self._save_preferences()
            self._refreshing = False
            current_id = self.current_chat_id if refresh else None
            anchor,bottom = self.virtual.anchor(),self.virtual.at_bottom()
            unchanged = bool(refresh and old and current_id
                and old.repository.chat_revision(current_id)==session.repository.chat_revision(current_id)
                and self._profile_stamp()==self._profile_mtime)
            if refresh:
                self._source_gen += 1
            self._loaded(session.source,session.infos,session.metas,{})
            self._wal_mtime = session.snapshot_stamp
            if old:
                old.retire()
            selected = next((i for i,x in enumerate(self.infos) if x.chat_id == current_id),None)
            if selected is not None:
                self.selected_idx = selected
                self._paint_rows()
                if unchanged:
                    self.virtual.pending.clear()
                    self.virtual.schedule()
                else:
                    self._start_preview(self.infos[selected],anchor=anchor,bottom=bottom,refresh=refresh)
            elif not refresh:
                self.current_chat_id = None
                self._pending_jump = None
                self.virtual.highlight = None
                self.virtual.reset(0)
                self.title_label.configure(text="选择左侧会话查看内容")
                self.export_btn.state(["disabled"])
                self.status.configure(text="")
        def failed(exc):
            if gen == self._source_gen:
                self._account_opening = False
                self.account_button.state(["!disabled"])
                self._error(exc)
        self.tasks.submit(work,ready,failed)

    def _select_chat(self, idx):
        if self.source is None or idx >= len(self.infos):
            return
        self.selected_idx = idx
        self._paint_rows()
        info = self.infos[idx]
        if info.chat_id != self.current_chat_id:
            self._start_preview(info)

    def _start_preview(self, info, *, anchor=None, bottom=True,refresh=False):
        if not refresh:
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
            if refresh:
                self.virtual.refresh(count,anchor=anchor,bottom=bottom)
            else:
                self.virtual.reset(count,anchor=anchor,bottom=bottom)
            if getattr(self,"_pending_jump",None) is not None:
                self.virtual.highlight = self._pending_jump
                self._pending_jump = None
            self._busy = False
            self.export_btn.state(["!disabled"] if count else ["disabled"])
            self.status.configure(text="")
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
            self.virtual.after_idle(lambda: self.virtual.image_ready(index,self._thumbs.get(key),key) if gen == self._render_gen else None)
            return
        if (gen,index,key) in self._image_pending:
            return
        self._image_pending.add((gen,index,key))
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
            self._image_pending.discard((gen,index,key))
            if gen != self._render_gen:
                return
            if image is not None:
                self._thumbs[key] = image
                self._thumbs.move_to_end(key)
                while len(self._thumbs)>64:
                    self._thumbs.popitem(last=False)
            self.virtual.image_ready(index,image,key)
        future = self.tasks.submit(decode,ready,self._error)
        future.add_done_callback(lambda _:release())

    def _open_export(self):
        if self.selected_idx is None or not self.virtual.count:
            return
        if self.export_mode:
            return
        self.export_mode = True
        self.selected_msgs.clear()
        self.date_start = self.date_end = None
        self.date_btn.configure(text="时间选择")
        self.export_btn.pack_forget()
        self.export_center.pack(side="left",before=self.status)
        self.export_right.pack(side="right")
        self.virtual.selection(True,self.selected_msgs)
        self._update_all_label()

    def _exit_export_mode(self):
        self.export_mode = False
        self.export_center.pack_forget()
        self.export_right.pack_forget()
        self.date_label.pack_forget()
        self.export_btn.pack(side="left",before=self.status)
        if hasattr(self,"virtual"):
            self.virtual.selection(False,self.selected_msgs)

    def _cancel_export(self):
        self.selected_msgs.clear()
        self._clear_date_filter()
        self._exit_export_mode()
        self.status.configure(text="")

    def _clear_date_filter(self):
        super()._clear_date_filter()
        self.date_btn.configure(text="时间选择")

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
        self.status.configure(text=f"已选择 {len(self.selected_msgs)} / {self.virtual.count} 条",foreground="#81798e")

    def _apply_dates(self, start, end):
        self.date_start,self.date_end = start,end
        chat = self.current_chat_id
        def ready(indices):
            self.selected_msgs = indices
            self.virtual.selection(True,indices)
            from datetime import datetime
            label = "—".join(datetime.fromtimestamp(value).strftime("%m/%d") if value is not None else "不限"
                               for value in (start,end))
            self.date_label.configure(text=label)
            if start is not None or end is not None:
                self.date_btn.configure(text="时间 · "+label)
            else:
                self.date_btn.configure(text="时间选择")
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
            FormatDialog(self.root,self.last_out,default_formats=self.preferences.formats,
                         summary=f"{info.title} · 已选择 {len(ids)} 条消息",on_ok=lambda formats,out:
                         self._export_selected(session,info,ids,start,end,formats,out))
        self._submit(session,lambda repo: repo.ids_for_indices(info.chat_id,selected),ready,gen=self._render_gen)

    def _export_selected(self, session, info, ids, start, end, formats, output):
        from qqexport.export.jobs import ExportJob
        self.remember_export(formats,output)
        if session.retired:
            messagebox.showinfo("数据源已更新","请重新选择消息后导出。")
            return
        job = ExportJob(session.source.account_uin,[info.chat_id],
            orchestrator.ExportOptions(formats=formats,out_base=output,start_ts=start,end_ts=end,message_ids=ids))
        self._jobs.append(job)
        self.status.configure(text="正在导出…",foreground="#333")
        def finished(_):
            if job in self._jobs:
                self._jobs.remove(job)
            task = job.tasks[0]
            if task.status=="done":
                self._export_done(job.last_report,output)
            elif task.status=="cancelled":
                self.status.configure(text="已取消导出")
            elif task.status=="skipped":
                self.status.configure(text="所选范围没有消息")
            else:
                messagebox.showerror("导出失败",task.error or "请检查输出目录和磁盘空间",parent=self.root)
                self.status.configure(text="导出失败",foreground="#b00")
        self._submit(session,lambda repo:job.run(repo),finished)

    def _watch_live(self):
        self._watch_id = None
        if self.session is None or self._closed:
            return
        stamp = self._current_mtime()
        if stamp and stamp != self._wal_mtime and not self._busy and not self.export_mode and not self._refreshing and not self._account_opening:
            self._refreshing = True
            self._open_account(self.source.meta["account"],refresh=True)
        self._schedule_watch()

    @staticmethod
    def _account_stamp(account):
        values = []
        for name in ("nt_msg.db","profile_info.db","group_info.db"):
            for suffix in ("","-wal"):
                try:
                    stat = (account.db_dir/(name+suffix)).stat()
                    values.append((stat.st_mtime_ns,stat.st_size))
                except OSError:
                    values.append(None)
        return tuple(values)

    def _current_mtime(self):
        account = self.source.meta.get("account") if self.source else None
        return self._account_stamp(account) if account else 0

    def _schedule_watch(self):
        if self._watch_id is not None:
            self.root.after_cancel(self._watch_id)
        self._watch_id = self.root.after(1000,self._watch_live)

    def close(self):
        if self._closed:
            return
        state = self.root.state()
        self.preferences.window_state = "zoomed" if state=="zoomed" else "normal"
        if state=="normal":
            self.preferences.geometry = self.root.geometry()
        self._save_preferences()
        self._closed = True
        self.accounts_panel.close()
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
        self.gui_avatars.tasks.close()
        if self.session:
            self.session.retire()
        self._finish_close()

    def _finish_close(self):
        if not self.tasks.idle() or not self.gui_avatars.tasks.idle():
            self.root.after(25,self._finish_close)
            return
        self.tasks.pool.shutdown(wait=True)
        self.gui_avatars.tasks.pool.shutdown(wait=True)
        self._thumbs.clear()
        self.virtual.pages.clear()
        self.root.destroy()
