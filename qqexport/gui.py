"""图形界面：左侧会话、右侧预览、底部导出；导出弹窗先选范围/记录，再选格式。"""
from __future__ import annotations

import base64
import calendar
import io
import os
import queue
import threading
import tkinter as tk
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

try:
    from PIL import Image, ImageDraw, ImageTk
    _HAS_PIL = True
except ImportError:  # GUI 仍可用，只是不显示 JPEG 图
    _HAS_PIL = False

from qqexport import config
from qqexport.core import discover, keystore
from qqexport.export import orchestrator, txt
from qqexport.export.session import ChatInfo, ExportSource
from qqexport.qq import avatars


def _parse_day(text: str, *, end: bool) -> int | None:
    text = text.strip()
    if not text:
        return None
    try:
        day = datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        raise ValueError(f"日期格式应为 YYYY-MM-DD：{text}")
    if end:
        day = day + timedelta(days=1)
        return int(day.timestamp()) - 1
    return int(day.timestamp())


class App:
    def __init__(self, root: tk.Tk, *, data_dir: Path | None = None,
                 uin: str | None = None):
        self.root = root
        self.data_dir = data_dir
        self.uin = uin
        self.source: ExportSource | None = None
        self.infos: list[ChatInfo] = []
        self.preview: list = []
        self.current_chat_id: str | None = None
        self.selected_idx: int | None = None
        self.chat_meta: dict[str, tuple[str, int | None]] = {}
        self.avatar_data: dict[str, bytes | None] = {}
        self._row_photos: dict[str, tk.PhotoImage] = {}
        self.row_widgets: list[dict] = []
        self._hover: int | None = None
        self._render_gen = 0
        self._follow = True
        self._watch_id: str | None = None
        self._refreshing = False
        self._wal_mtime = 0
        self._wal_path: Path | None = None
        self._db_path: Path | None = None
        self.export_mode = False
        self.selected_msgs: set[int] = set()
        self.check_vars: dict[int, tk.BooleanVar] = {}
        self.check_buttons: dict[int, tk.Checkbutton] = {}
        self.check_frames: dict[int, tk.Frame] = {}
        self.msg_spans: dict[int, tuple[int, int]] = {}
        self.date_start: int | None = None
        self.date_end: int | None = None
        self.last_out = str(Path.home() / "Documents")
        self._busy = False
        root.title("QQ 聊天记录导出")
        root.geometry("1120x700")
        root.minsize(960, 560)
        self._build()
        self._load_chats()

    # ---------- 布局 ----------
    def _build(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        outer = ttk.Frame(self.root, padding=10)
        outer.pack(fill="both", expand=True)
        paned = ttk.Panedwindow(outer, orient="horizontal")
        paned.pack(fill="both", expand=True)

        left = ttk.Frame(paned)
        paned.add(left, weight=2)
        ttk.Label(left, text="会话", font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w")
        box = ttk.Frame(left)
        box.pack(fill="both", expand=True, pady=4)
        self.canvas = tk.Canvas(box, bg="#ffffff", highlightthickness=0, width=300)
        sb = ttk.Scrollbar(box, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=sb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.inner = tk.Frame(self.canvas, bg="#ffffff")
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda _e: self.canvas.configure(
            scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(
            self._win, width=e.width))
        self.count_label = ttk.Label(left, text="正在读取会话…")
        self.count_label.pack(anchor="w")

        right = ttk.Frame(paned, padding=(6, 0, 0, 0))
        paned.add(right, weight=5)
        header = ttk.Frame(right)
        header.pack(fill="x")
        self.title_label = ttk.Label(header, text="选择左侧会话查看内容",
                                     font=("Microsoft YaHei UI", 11, "bold"))
        self.title_label.pack(side="left")
        self.export_center = ttk.Frame(header)
        self.all_btn = ttk.Button(self.export_center, text="全选",
                                  command=self._toggle_all)
        self.all_btn.pack(side="left", padx=(0, 8))
        self.date_btn = ttk.Button(self.export_center, text="日期选择",
                                   command=self._pick_dates)
        self.date_btn.pack(side="left")
        self.date_label = ttk.Label(self.export_center, text="已选择：不限",
                                    foreground="#666")
        pv = ttk.Frame(right)
        pv.pack(fill="both", expand=True, pady=(4, 6))
        self.text = tk.Text(pv, wrap="word", state="disabled",
                            font=("Microsoft YaHei UI", 10), padx=6, pady=4,
                            spacing1=1, spacing3=1,
                            selectbackground="#2f7cf6", selectforeground="#ffffff",
                            inactiveselectbackground="#b9d5ff",
                            insertwidth=0, cursor="arrow", takefocus=0)
        tsb = ttk.Scrollbar(pv, orient="vertical", command=self.text.yview)
        self.tsb = tsb
        self.text.configure(yscrollcommand=self._on_text_scroll)
        self.text.bind("<Button-1>", self._text_click)
        self.text.pack(side="left", fill="both", expand=True)
        tsb.pack(side="right", fill="y")
        self.sel_frame = tk.Frame(pv, bg="#ffffff")
        self.gutter = tk.Frame(pv, width=34, bg="#ffffff")
        self.gutter_canvas = tk.Canvas(self.gutter, width=34, bg="#ffffff",
                                       highlightthickness=0)
        self.gutter_canvas.pack(fill="both", expand=True)
        self.sel_canvas = tk.Canvas(self.sel_frame, bg="#ffffff",
                                    highlightthickness=0)
        self.sel_sb = ttk.Scrollbar(self.sel_frame, orient="vertical",
                                    command=self.sel_canvas.yview)
        self.sel_canvas.configure(yscrollcommand=self.sel_sb.set)
        self.sel_canvas.pack(side="left", fill="both", expand=True)
        self.sel_sb.pack(side="right", fill="y")
        self.sel_inner = tk.Frame(self.sel_canvas, bg="#ffffff")
        self.sel_win = self.sel_canvas.create_window(
            (0, 0), window=self.sel_inner, anchor="nw")
        self.sel_inner.bind("<Configure>", lambda _e: self.sel_canvas.configure(
            scrollregion=self.sel_canvas.bbox("all")))
        self.sel_canvas.bind("<Configure>", lambda e: self.sel_canvas.itemconfigure(
            self.sel_win, width=e.width))
        self.sel_canvas.bind("<MouseWheel>", self._sel_wheel)
        self.sel_rows: list[dict] = []
        self._emoji: dict[int, tk.PhotoImage] = {}
        self._avatars: dict[str, tk.PhotoImage] = {}
        self._img_seq = 0
        self._tag_config()
        bottom = ttk.Frame(right)
        bottom.pack(fill="x")
        self.export_btn = ttk.Button(bottom, text="导出…", command=self._open_export)
        self.export_btn.pack(side="left")
        self.export_right = ttk.Frame(bottom)
        self.next_btn = ttk.Button(self.export_right, text="下一步",
                                   command=self._next_step)
        self.next_btn.pack(side="left", padx=(0, 6))
        self.cancel_btn = ttk.Button(self.export_right, text="取消",
                                     command=self._cancel_export)
        self.cancel_btn.pack(side="left")
        self.status = ttk.Label(bottom, text="", foreground="#333")
        self.status.pack(side="left", padx=12)

    # ---------- 数据 ----------
    def _load_chats(self) -> None:
        self.export_btn.state(["disabled"])

        def work():
            try:
                accounts = discover.discover_accounts(explicit=self.data_dir)
                target = self.uin
                if target:
                    account = discover.select_account(accounts, target)
                else:
                    try:
                        account = discover.detect_active_account(accounts)
                    except discover.AmbiguousAccountError:
                        account = discover.select_account(
                            accounts, config.Config.load().uin)
                key = keystore.KeyStore().load(account.uin)
                if not key:
                    raise RuntimeError("没有缓存口令，请先运行 qqexport key scan 或 key set")
                source = ExportSource.open(account, key)
                infos = source.chats()
                metas: dict[str, tuple[str, int | None]] = {}
                avs: dict[str, bytes | None] = {}
                for info in infos:
                    try:
                        last = next(
                            (m for m in reversed(list(source.iter_messages(info.chat_id)))
                             if not m.is_system), None)
                        if last is not None:
                            body = " ".join(txt.segment_text(s)
                                            for s in last.segments).replace("\n", " ")
                            metas[info.chat_id] = (
                                f"{last.sender_name}：{body[:48]}", last.ts)
                    except Exception:
                        pass
                    try:
                        avs[info.chat_id] = (
                            avatars.fetch_group_avatar(info.account)
                            if info.kind == "group" else
                            avatars.fetch_avatar(info.account))
                    except Exception:
                        avs[info.chat_id] = None
            except Exception as exc:  # noqa: BLE001
                self.root.after(0, lambda: self._fail(exc))
                return
            self.root.after(0, lambda: self._loaded(source, infos, metas, avs))

        threading.Thread(target=work, daemon=True).start()

    def _on_text_scroll(self, first: str, last: str) -> None:
        self.tsb.set(first, last)
        try:
            self._follow = float(last) >= 0.99
        except ValueError:
            self._follow = False

    def _tag_config(self) -> None:
        self.text.tag_configure("day", foreground="#999", justify="center",
                                spacing1=8, spacing3=6)
        self.text.tag_configure("name", foreground="#777",
                                font=("Microsoft YaHei UI", 9))
        self.text.tag_configure("name_self", foreground="#777", justify="right",
                                font=("Microsoft YaHei UI", 9))
        self.text.tag_configure("bubble", background="#ffffff", lmargin1=8,
                                lmargin2=8, rmargin=90, spacing1=2, spacing3=4)
        self.text.tag_configure("bubble_self", background="#95ec69",
                                justify="right", lmargin1=90, lmargin2=8,
                                spacing1=2, spacing3=4)
        self.text.tag_configure("quote", background="#f0f0f0", foreground="#555",
                                lmargin1=8, lmargin2=8, rmargin=90)
        self.text.tag_configure("media", background="#f5f5f5", foreground="#333",
                                lmargin1=8, lmargin2=8, rmargin=90,
                                spacing1=2, spacing3=4)
        self.text.tag_configure("meta", foreground="#999",
                                font=("Microsoft YaHei UI", 8))
        self.text.tag_configure("recall", foreground="#999", justify="center",
                                spacing1=6, spacing3=6)
        self.text.tag_configure("check", foreground="#2f7cf6",
                                font=("Segoe UI Symbol", 10))
        self.text.tag_configure("check_on", foreground="#ffffff",
                                background="#2f7cf6",
                                font=("Segoe UI Symbol", 10))
        self.text.tag_configure("rowsel", background="#e6f0ff")
        # 气泡 tag 的背景会盖住默认的选中高亮；显式把 sel 放到最高优先级
        self.text.tag_configure("sel", background="#2f7cf6", foreground="#ffffff")
        self.text.tag_raise("sel")

    def _loaded(self, source, infos, metas, avs) -> None:
        self.source = source
        self.infos = infos
        self.chat_meta = metas
        self.avatar_data = avs
        for child in self.inner.winfo_children():
            child.destroy()
        self.row_widgets = []
        for i, info in enumerate(infos):
            self._make_row(i, info)
        self.selected_idx = None
        self._hover = None
        self.canvas.yview_moveto(0)
        account = source.meta.get("account")
        who = getattr(account, "uin", "?")
        self.count_label.configure(text=f"已识别 QQ {who} · 共 {len(infos)} 个会话")
        self._wal_path = account.db_dir / "nt_msg.db-wal"
        self._db_path = account.db_dir / "nt_msg.db"
        self._profile_paths = [account.db_dir / "profile_info.db",
                               account.db_dir / "group_info.db"]
        self._profile_mtime = self._profile_stamp()
        self._wal_mtime = self._current_mtime()
        self._schedule_watch()

    def _profile_stamp(self) -> int:
        best = 0
        for p in getattr(self, "_profile_paths", []):
            for path in (p, Path(str(p) + "-wal")):
                try:
                    if path.is_file():
                        best = max(best, path.stat().st_mtime_ns)
                except OSError:
                    pass
        return best

    def _current_mtime(self) -> int:
        best = self._profile_stamp()
        for path in (self._wal_path, self._db_path):
            try:
                if path is not None and path.is_file():
                    best = max(best, path.stat().st_mtime_ns)
            except OSError:
                pass
        return best

    def _schedule_watch(self) -> None:
        if self._watch_id is not None:
            try:
                self.root.after_cancel(self._watch_id)
            except tk.TclError:
                pass
        self._watch_id = self.root.after(2500, self._watch_live)

    def _watch_live(self) -> None:
        self._watch_id = None
        if self.source is None:
            return
        mtime = self._current_mtime()
        if mtime and mtime != self._wal_mtime and not self._refreshing and not self._busy:
            self._wal_mtime = mtime
            self._refreshing = True
            threading.Thread(target=self._refresh_worker, daemon=True).start()
        self._schedule_watch()

    def _refresh_worker(self) -> None:
        try:
            account = self.source.meta.get("account")
            key = keystore.KeyStore().load(account.uin)
            new_source = ExportSource.open(account, key)
            infos = new_source.chats()
            current_id = self.current_chat_id
            profile_stamp = self._profile_stamp()
            profile_changed = profile_stamp != self._profile_mtime
            avs = self.avatar_data
            if profile_changed:
                avatars.clear_cache()
                avs = {}
                for info in infos:
                    try:
                        avs[info.chat_id] = (
                            avatars.fetch_group_avatar(info.account)
                            if info.kind == "group" else
                            avatars.fetch_avatar(info.account))
                    except Exception:
                        avs[info.chat_id] = None
            metas: dict[str, tuple[str, int | None]] = {}
            for info in infos:
                try:
                    last = next(
                        (m for m in reversed(list(new_source.iter_messages(info.chat_id)))
                         if not m.is_system), None)
                    if last is not None:
                        body = " ".join(txt.segment_text(s)
                                        for s in last.segments).replace("\n", " ")
                        metas[info.chat_id] = (
                            f"{last.sender_name}：{body[:48]}", last.ts)
                except Exception:
                    pass
            last = self.preview[-1] if self.preview else None
            fresh = []
            if current_id and last is not None:
                fresh = [m for m in new_source.iter_messages(current_id)
                         if not m.is_system
                         and (m.ts, m.msg_id) > (last.ts, last.msg_id)]
                if fresh:
                    try:
                        fresh = avatars.add_avatars(fresh)
                    except Exception:
                        pass
        except Exception as exc:  # noqa: BLE001
            self.root.after(0, lambda: self._refresh_failed(exc))
            return
        self.root.after(0, lambda: self._apply_refresh(
            new_source, infos, current_id, fresh, metas,
            profile_changed, profile_stamp, avs))

    def _apply_refresh(self, new_source, infos, current_id, fresh, metas,
                       profile_changed=False, profile_stamp=0, avs=None) -> None:
        old = self.source
        selected_id = (self.infos[self.selected_idx].chat_id
                       if self.selected_idx is not None and self.selected_idx < len(self.infos)
                       else current_id)
        self.source = new_source
        if old is not None:
            old.close()
        self.infos = infos
        self.chat_meta = metas
        if profile_changed:
            self.avatar_data = avs or {}
            self._profile_mtime = profile_stamp
        for child in self.inner.winfo_children():
            child.destroy()
        self.row_widgets = []
        for i, info in enumerate(infos):
            self._make_row(i, info)
        self.selected_idx = next(
            (i for i, info in enumerate(infos) if info.chat_id == selected_id), None)
        self._paint_rows()
        if profile_changed and self.selected_idx is not None:
            info = infos[self.selected_idx]
            self.title_label.configure(text=info.title)
            self._start_preview(info)
            self.status.configure(text="资料/头像已更新", foreground="#060")
        elif fresh and selected_id == current_id:
            self.text.configure(state="normal")
            self.text.insert("end", "\n── 新消息 ──\n", "day")
            self.preview.extend(fresh)
            for m in fresh:
                self._append_message(m)
            self.text.configure(state="disabled")
            if self._follow:
                self.text.yview_moveto(1.0)
            self.status.configure(text=f"已收到 {len(fresh)} 条新消息", foreground="#060")
        self._refreshing = False
        self._wal_mtime = self._current_mtime()

    def _refresh_failed(self, exc: Exception) -> None:
        self._refreshing = False
        self.status.configure(text=f"实时更新失败：{type(exc).__name__}",
                              foreground="#b00")

    def _fail(self, exc: Exception) -> None:
        self.count_label.configure(text="读取失败")
        self.status.configure(text=str(exc), foreground="#b00")
        messagebox.showerror("读取失败", str(exc))

    def _fmt_time(self, ts: int | None) -> str:
        if not ts:
            return ""
        dt = datetime.fromtimestamp(ts)
        if dt.date() == datetime.now().date():
            return dt.strftime("%H:%M")
        return dt.strftime("%m-%d")

    def _make_row(self, idx: int, info: ChatInfo) -> None:
        bg = "#ffffff"
        row = tk.Frame(self.inner, bg=bg, cursor="hand2")
        row.pack(fill="x", padx=5, pady=2)
        row.columnconfigure(1, weight=1)
        photo = self._row_avatar(info)
        avatar = tk.Label(row, image=photo, bg=bg, width=44, height=44)
        avatar.grid(row=0, column=0, rowspan=2, padx=(8, 8), pady=8, sticky="n")
        title = tk.Label(row, text=info.title, bg=bg, fg="#1b1b1b",
                         font=("Microsoft YaHei UI", 10, "bold"),
                         anchor="w", justify="left", wraplength=170)
        title.grid(row=0, column=1, sticky="w", pady=(8, 0))
        preview, ts = self.chat_meta.get(info.chat_id, ("", info.last_ts))
        time_lbl = tk.Label(row, text=self._fmt_time(ts), bg=bg, fg="#999",
                            font=("Microsoft YaHei UI", 8))
        time_lbl.grid(row=0, column=2, sticky="ne", padx=(4, 8), pady=(9, 0))
        sub = tk.Label(row, text=preview or "（无消息）", bg=bg, fg="#888",
                       font=("Microsoft YaHei UI", 9), anchor="w",
                       justify="left", wraplength=210)
        sub.grid(row=1, column=1, columnspan=2, sticky="w", pady=(1, 8))
        widgets = (row, avatar, title, time_lbl, sub)
        for w in widgets:
            w.bind("<Button-1>", lambda _e, i=idx: self._select_chat(i))
            w.bind("<Enter>", lambda _e, i=idx: self._hover_row(i, True))
            w.bind("<Leave>", lambda _e, i=idx: self._hover_row(i, False))
            w.bind("<MouseWheel>", self._wheel)
        self.row_widgets.append({"frame": row, "widgets": widgets,
                                 "labels": (avatar, title, time_lbl, sub)})

    def _wheel(self, event) -> None:
        self.canvas.yview_scroll(int(-event.delta / 120), "units")

    def _row_avatar(self, info: ChatInfo) -> tk.PhotoImage | None:
        data = self.avatar_data.get(info.chat_id)
        if data and _HAS_PIL:
            try:
                img = Image.open(io.BytesIO(data)).convert("RGB")
                side = min(img.width, img.height)
                img = img.crop(((img.width - side) // 2, (img.height - side) // 2,
                                (img.width + side) // 2, (img.height + side) // 2))
                img = img.resize((44, 44), Image.LANCZOS)
                mask = Image.new("L", (44, 44), 0)
                ImageDraw.Draw(mask).ellipse((0, 0, 43, 43), fill=255)
                rounded = Image.new("RGBA", (44, 44), (0, 0, 0, 0))
                rounded.paste(img, (0, 0), mask)
                photo = ImageTk.PhotoImage(rounded)
            except Exception:
                photo = self._placeholder_avatar(info)
        else:
            photo = self._placeholder_avatar(info)
        self._row_photos[info.chat_id] = photo
        return photo

    def _placeholder_avatar(self, info: ChatInfo) -> tk.PhotoImage:
        colors = ["#5b8ff9", "#61c9a8", "#f6bd16", "#e8684a", "#9270ca", "#6dc8ec"]
        color = colors[sum(map(ord, info.title)) % len(colors)]
        img = Image.new("RGB", (44, 44), color)
        draw = ImageDraw.Draw(img)
        draw.ellipse((0, 0, 43, 43), fill=color)
        draw.text((16, 12), (info.title or "?")[:1], fill="#ffffff")
        return ImageTk.PhotoImage(img)

    def _paint_rows(self) -> None:
        for i, row in enumerate(self.row_widgets):
            if i == self.selected_idx:
                bg = "#e6f0ff"
            elif i == getattr(self, "_hover", None):
                bg = "#f3f3f3"
            else:
                bg = "#ffffff"
            row["frame"].configure(bg=bg)
            for w in row["widgets"]:
                w.configure(bg=bg)

    def _hover_row(self, idx: int, entering: bool) -> None:
        self._hover = idx if entering else None
        self._paint_rows()

    def _select_chat(self, idx: int) -> None:
        if self.source is None or idx >= len(self.infos):
            return
        self.selected_idx = idx
        self._paint_rows()
        info = self.infos[idx]
        if info.chat_id == self.current_chat_id and self.preview:
            return
        self._start_preview(info)

    def _start_preview(self, info: ChatInfo) -> None:
        self.current_chat_id = info.chat_id
        self.title_label.configure(text=info.title)
        self._busy = True
        self.export_btn.state(["disabled"])
        self._render_gen += 1
        gen = self._render_gen
        self._stream_q = queue.Queue()
        q = self._stream_q
        source = self.source
        chat_id = info.chat_id
        self._stream_done = False
        self._render_day = None
        self._follow = True
        self.preview = []
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")

        def work():
            try:
                msgs = [m for m in source.iter_messages(chat_id)
                        if not m.is_system]
                size = 120
                chunks = [msgs[max(0, end - size):end]
                          for end in range(len(msgs), 0, -size)]
                for i, chunk in enumerate(chunks):
                    if gen != self._render_gen:
                        return
                    try:
                        chunk = avatars.add_avatars(chunk)
                    except Exception:
                        pass
                    q.put((chunk, i == len(chunks) - 1))
            finally:
                q.put(None)

        threading.Thread(target=work, daemon=True).start()
        self._drain_stream(gen)

    def _drain_stream(self, gen: int) -> None:
        if gen != self._render_gen:
            return
        chunks = []
        done = False
        while True:
            try:
                item = self._stream_q.get_nowait()
            except queue.Empty:
                break
            if item is None:
                done = True
                break
            chunks.append(item)
        for chunk, last in chunks:
            # 新块比已显示内容更早，拼到前面；最新一块先显示
            self.preview = chunk + self.preview
            self._rerender()
            if self._follow:
                self.text.see("end")
            if last:
                self._stream_done = True
        if done or self._stream_done:
            self.text.see("end")
            self.text.configure(state="disabled")
            self._busy = False
            self.export_btn.state(["!disabled"])
            return
        self.root.after(15, lambda: self._drain_stream(gen))

    def _rerender(self, keep_scroll: bool = True) -> None:
        top = self.text.index("@0,0") if keep_scroll else "1.0"
        info = self.text.dlineinfo(top) if keep_scroll else None
        offset = info[1] if info else 0
        at_bottom = keep_scroll and self.text.yview()[1] >= 0.99
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.check_vars = {}
        self.check_buttons = {}
        self.check_frames = {}
        self.msg_spans = {}
        self._render_day = None
        for i, m in enumerate(self.preview):
            try:
                self._append_message(m, i)
            except Exception as exc:  # noqa: BLE001
                self.text.insert("end", f"[渲染失败：{type(exc).__name__}]\n", "meta")
        if at_bottom:
            self.text.see("end")
        else:
            try:
                self.text.yview(top)
                if offset:
                    self.text.yview_scroll(-offset, "pixels")
            except tk.TclError:
                pass
        self.text.configure(state="normal" if self.export_mode else "disabled")

    def _append_message(self, m, idx: int | None = None) -> None:
        start = self.text.index("end-1c")
        day = datetime.fromtimestamp(m.ts).strftime("%Y-%m-%d")
        if day != self._render_day:
            self.text.insert("end", f"{day}\n", "day")
            self._render_day = day
        if getattr(m, "is_recall", False):
            text = next((s.text for s in m.segments
                         if s.kind == "recall" and s.text), "撤回了一条消息")
            self.text.insert("end", f"── {text} ──\n", "recall")
            return
        tag = "bubble_self" if m.is_self else "bubble"
        name_tag = "name_self" if m.is_self else "name"
        stamp = datetime.fromtimestamp(m.ts).strftime("%H:%M:%S")
        avatar = self._avatar_photo(m.avatar_data)
        if avatar is not None:
            self.text.image_create("end", image=avatar)
            self.text.insert("end", " ")
        self.text.insert("end", f"{m.sender_name}  {stamp}\n", name_tag)
        self._insert_segments(m, tag, idx)
        self.text.insert("end", "\n")
        if idx is not None:
            self.msg_spans[idx] = (int(start.split(".")[0]),
                                   int(self.text.index("end-1c").split(".")[0]))

    def _preview_ready(self, msgs) -> None:
        self.preview = msgs
        self._render_gen += 1
        gen = self._render_gen
        self._render_msgs = msgs
        self._render_pos = 0
        self._render_day = None
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self._render_next(gen)

    def _render_next(self, gen: int) -> None:
        if gen != self._render_gen:
            return
        end = min(self._render_pos + 60, len(self._render_msgs))
        for i, m in enumerate(self._render_msgs[self._render_pos:end],
                              start=self._render_pos):
            self._append_message(m, i)
        self._render_pos = end
        if end < len(self._render_msgs):
            self.root.after(5, lambda: self._render_next(gen))
            return
        self.text.see("end")
        self.text.configure(state="disabled")
        self._busy = False
        self.export_btn.state(["!disabled"])

    def _insert_segments(self, msg, tag: str, idx: int | None = None) -> None:
        for seg in msg.segments:
            if seg.kind == "reply":
                if seg.quoted is not None:
                    quote = " ".join(
                        txt.segment_text(s) for s in seg.quoted.segments).strip()
                    self.text.insert("end", f"↪ {seg.quoted.sender_name}：{quote}\n",
                                     "quote")
                if seg.text:
                    self.text.insert("end", seg.text, tag)
                continue
            if seg.kind == "face" and seg.face_id is not None:
                photo = self._emoji_photo(seg.face_id)
                if photo is not None:
                    self.text.image_create("end", image=photo)
                    self.text.insert("end", " ")
                    continue
            if seg.kind == "image":
                photo = self._image_photo(seg.media)
                if photo is not None:
                    imgtag = f"img{self._img_seq}"
                    self._img_seq += 1
                    start = self.text.index("end-1c")
                    self.text.image_create("end", image=photo)
                    self.text.tag_add(imgtag, start, "end-1c")
                    self.text.tag_bind(imgtag, "<Button-1>",
                                       lambda _e, i=idx, m=seg.media:
                                       self._image_click(i, m))
                    self.text.tag_bind(imgtag, "<Enter>",
                                       lambda _e: self.text.configure(cursor="hand2"))
                    self.text.tag_bind(imgtag, "<Leave>",
                                       lambda _e: self.text.configure(cursor=""))
                    self.text.insert("end", "\n")
                else:
                    self.text.insert("end", "[图片]（本机无原图）", "media")
                continue
            if seg.kind == "file":
                self.text.insert("end", "[文件]", "media")
                continue
            text = txt.segment_text(seg)
            if text:
                self.text.insert("end", text, tag)

    def _emoji_photo(self, face_id: int) -> tk.PhotoImage | None:
        if face_id in self._emoji:
            return self._emoji[face_id]
        nt_data = self.source.meta.get("nt_data") if self.source else None
        if not nt_data:
            return None
        path = avatars.emoji_path(Path(nt_data), face_id)
        if path is None:
            return None
        try:
            photo = tk.PhotoImage(file=str(path))
        except tk.TclError:
            return None
        self._emoji[face_id] = photo
        return photo

    def _avatar_photo(self, data_uri: str | None) -> tk.PhotoImage | None:
        if not data_uri or not _HAS_PIL:
            return None
        if data_uri in self._avatars:
            return self._avatars[data_uri]
        try:
            raw = base64.b64decode(data_uri.split(",", 1)[1])
            img = Image.open(io.BytesIO(raw)).convert("RGB")
            side = min(img.width, img.height)
            img = img.crop(((img.width - side) // 2, (img.height - side) // 2,
                            (img.width + side) // 2, (img.height + side) // 2))
            img = img.resize((34, 34), Image.LANCZOS)
            mask = Image.new("L", (34, 34), 0)
            ImageDraw.Draw(mask).ellipse((0, 0, 33, 33), fill=255)
            rounded = Image.new("RGBA", (34, 34), (0, 0, 0, 0))
            rounded.paste(img, (0, 0), mask)
            photo = ImageTk.PhotoImage(rounded)
        except Exception:
            return None
        self._avatars[data_uri] = photo
        return photo

    def _text_click(self, event):
        if not self.export_mode:
            return None
        try:
            index = self.text.index(f"@{event.x},{event.y}")
            info = self.text.dlineinfo(index)
            if info is None or event.y > info[1] + info[3]:
                return "break"
            line = int(index.split(".")[0])
        except tk.TclError:
            return "break"
        for idx, (first, last) in self.msg_spans.items():
            if first <= line <= last:
                self._toggle_check(idx)
                return "break"
        return "break"

    def _image_click(self, idx: int | None, media) -> None:
        if self.export_mode:
            return
        self._view_media(media)

    def _view_media(self, media) -> None:
        if media is None:
            return
        if media.source is not None and media.source.is_file():
            os.startfile(media.source)  # type: ignore[attr-defined]
        elif media.data_uri:
            ImageViewer(self.root, data_uri=media.data_uri)

    def _view_image(self, source: Path | None) -> None:
        if source is None or not source.is_file():
            return
        ImageViewer(self.root, source)

    def _image_photo(self, media) -> tk.PhotoImage | None:
        if media is None:
            return None
        if media.data_uri and _HAS_PIL:
            try:
                raw = base64.b64decode(media.data_uri.split(",", 1)[1])
                img = Image.open(io.BytesIO(raw))
                img.thumbnail((240, 240))
                photo = ImageTk.PhotoImage(img)
            except Exception:
                return None
            self._emoji[id(photo)] = photo
            return photo
        if media.source is None or media.missing:
            return None
        try:
            img = Image.open(media.source)
            img.thumbnail((240, 240))
            photo = ImageTk.PhotoImage(img)
        except Exception:
            return None
        self._emoji[id(photo)] = photo  # 保持引用
        return photo

    def _set_text(self, content: str) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", content)
        self.text.see("end")
        self.text.configure(state="disabled")

    # ---------- 导出 ----------
    def _open_export(self) -> None:
        if self.selected_idx is None or not self.preview:
            messagebox.showinfo("请选择会话", "请先在左侧选择会话。")
            return
        self.export_mode = True
        self.selected_msgs = set()
        self.date_start = self.date_end = None
        self.all_btn.configure(text="全选")
        self.date_label.pack_forget()
        self.export_center.pack(side="right")
        self.export_right.pack(side="right")
        self.status.configure(text="勾选消息，或使用全选/日期选择")
        self.text.pack_forget()
        self.tsb.pack_forget()
        self.gutter.pack(side="left", fill="y")
        self.text.pack(side="left", fill="both", expand=True)
        self.tsb.pack(side="right", fill="y")
        self.root.update_idletasks()
        self._build_gutter()

    def _text_pixels(self, start: str, end: str) -> int:
        try:
            value = self.text.count(start, end, "ypixels")
            if isinstance(value, tuple):
                value = value[0] if value else 0
            return int(value or 0)
        except Exception:
            return 0

    def _build_gutter(self) -> None:
        self.gutter_canvas.delete("all")
        self.check_vars = {}
        self.check_buttons = {}
        total = self._text_pixels("1.0", "end")
        width = 30
        self.gutter_canvas.configure(scrollregion=(0, 0, width, max(total, 1)))
        for idx in range(len(self.preview)):
            first = self.msg_spans.get(idx, (None, None))[0]
            if first is None:
                continue
            y = self._text_pixels("1.0", f"{first}.0")
            var = tk.BooleanVar(value=idx in self.selected_msgs)
            cb = tk.Checkbutton(
                self.gutter_canvas, variable=var, bg="#ffffff",
                activebackground="#ffffff", padx=0, pady=0, bd=0,
                highlightthickness=0,
                command=lambda i=idx, v=var: self._checkbox_toggled(i, v))
            self.check_vars[idx] = var
            self.check_buttons[idx] = cb
            self.gutter_canvas.create_window(6, y, window=cb, anchor="nw")
        self._sync_gutter_scroll()

    def _sync_gutter_scroll(self) -> None:
        if not self.export_mode:
            return
        try:
            self.gutter_canvas.yview_moveto(self.text.yview()[0])
        except tk.TclError:
            pass

    def _exit_export_mode(self) -> None:
        self.export_mode = False
        self.export_center.pack_forget()
        self.export_right.pack_forget()
        self.date_label.pack_forget()
        self.gutter.pack_forget()
        for cb in self.check_buttons.values():
            cb.destroy()
        self.check_buttons = {}
        self.check_vars = {}
        for idx, (first, last) in self.msg_spans.items():
            self.text.tag_remove("rowsel", f"{first}.0", f"{last}.end+1c")
        self.text.pack(side="left", fill="both", expand=True)
        self.tsb.pack(side="right", fill="y")
        self.text.configure(state="disabled")

    def _sel_wheel(self, event) -> str:
        self.sel_canvas.yview_scroll(int(-event.delta / 120), "units")
        return "break"

    def _build_selection_rows(self) -> None:
        for child in self.sel_inner.winfo_children():
            child.destroy()
        self.sel_rows = []
        for idx, m in enumerate(self.preview):
            row = tk.Frame(self.sel_inner, bg="#ffffff")
            row.pack(fill="x", padx=4, pady=2)
            var = tk.BooleanVar(value=idx in self.selected_msgs)
            cb = tk.Checkbutton(
                row, variable=var, bg="#ffffff", activebackground="#ffffff",
                padx=0, pady=0, bd=0, highlightthickness=0,
                command=lambda i=idx, v=var: self._checkbox_toggled(i, v))
            cb.pack(side="left", anchor="n", padx=(2, 6), pady=4)
            avatar = tk.Label(row, bg="#ffffff")
            photo = self._avatar_photo(m.avatar_data)
            if photo is not None:
                avatar.configure(image=photo)
            avatar.pack(side="left", anchor="n", padx=(0, 6), pady=2)
            content = tk.Frame(row, bg="#ffffff")
            content.pack(side="left", fill="x", expand=True)
            stamp = datetime.fromtimestamp(m.ts).strftime("%H:%M:%S")
            head = tk.Label(content, text=f"{m.sender_name}  {stamp}",
                            bg="#ffffff", fg="#777",
                            font=("Microsoft YaHei UI", 9), anchor="w")
            head.pack(fill="x")
            body = tk.Label(
                content, text=self._row_text(m), bg="#ffffff", fg="#222",
                font=("Microsoft YaHei UI", 10), anchor="w", justify="left",
                wraplength=760)
            body.pack(fill="x")
            widgets = [row, avatar, content, head, body]
            for w in widgets + [cb]:
                w.bind("<MouseWheel>", self._sel_wheel)
                w.bind("<Button-1>", lambda _e, i=idx: self._toggle_check(i))
            self.sel_rows.append({"frame": row, "var": var, "cb": cb,
                                  "bg": widgets})
        self._apply_selection_visuals()

    def _row_text(self, m) -> str:
        if getattr(m, "is_recall", False):
            return "── 撤回了一条消息 ──"
        parts = []
        for s in m.segments:
            if s.kind == "reply" and s.quoted is not None:
                quote = " ".join(txt.segment_text(x)
                                 for x in s.quoted.segments).strip()
                parts.append(f"↪ {s.quoted.sender_name}：{quote}")
            parts.append(txt.segment_text(s))
        return "\n".join(p for p in parts if p)


    def _cancel_export(self) -> None:
        self._exit_export_mode()
        self.status.configure(text="已取消选择")

    def _clear_date_filter(self) -> None:
        self.date_start = self.date_end = None
        self.date_label.pack_forget()

    def _toggle_all(self) -> None:
        self._clear_date_filter()
        if len(self.selected_msgs) == len(self.preview):
            self.selected_msgs.clear()
        else:
            self.selected_msgs = set(range(len(self.preview)))
        self.all_btn.configure(
            text="全不选" if len(self.selected_msgs) == len(self.preview)
            else "全选")
        self._apply_selection_visuals()
        self._update_all_label()

    def _capture_scroll(self):
        top = self.text.index("@0,0")
        info = self.text.dlineinfo(top)
        return top, (info[1] if info else 0), self.text.yview()[1] >= 0.99

    def _restore_scroll(self, anchor) -> None:
        top, offset, at_bottom = anchor
        if at_bottom:
            self.text.see("end")
            return
        try:
            self.text.yview(top)
            if offset:
                self.text.yview_scroll(-offset, "pixels")
        except tk.TclError:
            pass

    def _make_check(self, idx: int) -> None:
        frame = self.check_frames.get(idx)
        if frame is None or idx in self.check_buttons:
            return
        var = tk.BooleanVar(value=idx in self.selected_msgs)
        cb = tk.Checkbutton(
            frame, variable=var, padx=0, pady=0, bd=0, highlightthickness=0,
            bg="#ffffff", activebackground="#ffffff",
            command=lambda i=idx, v=var: self._checkbox_toggled(i, v))
        cb.pack()
        self.check_vars[idx] = var
        self.check_buttons[idx] = cb

    def _apply_selection_visuals(self, indices=None) -> None:
        items = range(len(self.preview)) if indices is None else indices
        for idx in items:
            var = self.check_vars.get(idx)
            if var is not None:
                var.set(idx in self.selected_msgs)
            cb = self.check_buttons.get(idx)
            if cb is not None:
                bg = "#e6f0ff" if idx in self.selected_msgs else "#ffffff"
                cb.configure(bg=bg, activebackground=bg)
            span = self.msg_spans.get(idx)
            if span:
                first, last = span
                if idx in self.selected_msgs:
                    self.text.tag_add("rowsel", f"{first}.0", f"{last}.end+1c")
                    self.text.tag_raise("rowsel")
                else:
                    self.text.tag_remove("rowsel", f"{first}.0",
                                         f"{last}.end+1c")

    def _update_all_label(self) -> None:
        self.all_btn.configure(
            text="全不选" if len(self.selected_msgs) == len(self.preview)
            else "全选")

    def _checkbox_toggled(self, idx: int, var: tk.BooleanVar) -> None:
        self._clear_date_filter()
        if var.get():
            self.selected_msgs.add(idx)
        else:
            self.selected_msgs.discard(idx)
        self._apply_selection_visuals([idx])
        self._update_all_label()

    def _toggle_check(self, idx: int) -> None:
        self._clear_date_filter()
        if idx in self.selected_msgs:
            self.selected_msgs.discard(idx)
        else:
            self.selected_msgs.add(idx)
        self._apply_selection_visuals([idx])
        self._update_all_label()

    def _pick_dates(self) -> None:
        DateRangeDialog(self.root, self.date_start, self.date_end,
                        on_ok=self._apply_dates)

    def _apply_dates(self, start: int | None, end: int | None) -> None:
        self.date_start, self.date_end = start, end
        self.selected_msgs = {
            i for i, m in enumerate(self.preview)
            if (start is None or m.ts >= start)
            and (end is None or m.ts <= end)}
        self.all_btn.configure(
            text="全不选" if len(self.selected_msgs) == len(self.preview)
            else "全选")
        if start and end:
            a = datetime.fromtimestamp(start).strftime("%Y，%m，%d")
            b = datetime.fromtimestamp(end).strftime("%Y，%m，%d")
            self.date_label.configure(text=f"已选择：{a}-{b}")
            self.date_label.pack(side="left", padx=(8, 0))
        elif start:
            a = datetime.fromtimestamp(start).strftime("%Y，%m，%d")
            self.date_label.configure(text=f"已选择：{a}-不限")
            self.date_label.pack(side="left", padx=(8, 0))
        else:
            self.date_label.pack_forget()
        self._apply_selection_visuals()
        self._update_all_label()
        self.status.configure(text=f"已按日期勾选 {len(self.selected_msgs)} 条")

    def _next_step(self) -> None:
        if not self.selected_msgs:
            messagebox.showinfo("未选择消息", "请至少勾选一条消息。")
            return
        ids = {self.preview[i].msg_id for i in self.selected_msgs}
        info = self.infos[self.selected_idx]
        start, end = self.date_start, self.date_end
        FormatDialog(self.root, self.last_out,
                     on_ok=lambda formats, out: self._export(
                         info, ids, start, end, formats, out))

    def _export(self, info: ChatInfo, message_ids, start_ts, end_ts,
                formats: set[str], out_base: Path) -> None:
        self.last_out = str(out_base)
        self.status.configure(text="正在导出…", foreground="#333")

        def work():
            try:
                rep = orchestrator.export_chats(
                    self.source, [info.chat_id], orchestrator.ExportOptions(
                        formats=formats, out_base=out_base,
                        start_ts=start_ts, end_ts=end_ts,
                        message_ids=message_ids))
            except Exception as exc:  # noqa: BLE001
                self.root.after(0, lambda: self._export_failed(exc))
                return
            self.root.after(0, lambda: self._export_done(rep, out_base))

        threading.Thread(target=work, daemon=True).start()

    def _export_done(self, rep, out_base: Path) -> None:
        folder = out_base / orchestrator.ROOT_FOLDER
        self.status.configure(text=rep.to_text(), foreground="#060")
        messagebox.showinfo("导出完成", f"已导出 {rep.chats_exported} 个会话\n\n{folder}")
        try:
            os.startfile(folder)  # type: ignore[attr-defined]
        except OSError:
            pass

    def _export_failed(self, exc: Exception) -> None:
        self.status.configure(text=str(exc), foreground="#b00")
        messagebox.showerror("导出失败", str(exc))

    def close(self) -> None:
        if self.source is not None:
            self.source.close()
        self.root.destroy()


class ExportDialog(tk.Toplevel):
    """两步：范围/记录 → 格式/位置。"""

    def __init__(self, parent, app: App, info: ChatInfo, messages,
                 *, default_out: str, on_export):
        super().__init__(parent)
        self.info = info
        self.messages = messages
        self.on_export = on_export
        self.title("选择导出内容")
        self.geometry("720x560")
        self.transient(parent)
        self.grab_set()
        self.out_var = tk.StringVar(value=default_out)
        self.start_var = tk.StringVar()
        self.end_var = tk.StringVar()
        self.fmt_var = tk.StringVar(value="html")
        self._step1()

    def _clear(self) -> None:
        for child in self.winfo_children():
            child.destroy()

    def _step1(self) -> None:
        self._clear()
        ttk.Label(self, text=f"{self.info.title}（{self.info.account}，"
                             f"{len(self.messages)} 条）",
                  font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w", padx=12, pady=(12, 4))
        ttk.Label(self, text="时间范围（YYYY-MM-DD，留空=不限）").pack(anchor="w", padx=12)
        row = ttk.Frame(self)
        row.pack(fill="x", padx=12, pady=4)
        ttk.Label(row, text="从").pack(side="left")
        ttk.Entry(row, textvariable=self.start_var, width=14).pack(side="left", padx=4)
        ttk.Label(row, text="到").pack(side="left")
        ttk.Entry(row, textvariable=self.end_var, width=14).pack(side="left", padx=4)
        ttk.Button(row, text="全部时间", command=self._all_time).pack(side="left", padx=8)
        ttk.Label(self, text="或者：在下面选择具体记录（不选=按时间范围/全部）").pack(
            anchor="w", padx=12, pady=(6, 0))
        box = ttk.Frame(self)
        box.pack(fill="both", expand=True, padx=12, pady=4)
        self.msg_list = tk.Listbox(box, selectmode="extended",
                                   font=("Microsoft YaHei UI", 9))
        sb = ttk.Scrollbar(box, orient="vertical", command=self.msg_list.yview)
        self.msg_list.configure(yscrollcommand=sb.set)
        self.msg_list.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        for m in self.messages:
            self.msg_list.insert("end", txt.format_line(m)[:160])
        btns = ttk.Frame(self)
        btns.pack(fill="x", padx=12, pady=8)
        ttk.Button(btns, text="全选", command=lambda: self.msg_list.selection_set(0, "end")).pack(side="left")
        ttk.Button(btns, text="清空选择", command=lambda: self.msg_list.selection_clear(0, "end")).pack(side="left", padx=4)
        ttk.Button(btns, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(btns, text="下一步：选格式", command=self._to_step2).pack(side="right", padx=6)

    def _all_time(self) -> None:
        self.start_var.set("")
        self.end_var.set("")
        self.msg_list.selection_clear(0, "end")

    def _to_step2(self) -> None:
        try:
            self.start_ts = _parse_day(self.start_var.get(), end=False)
            self.end_ts = _parse_day(self.end_var.get(), end=True)
            if self.start_ts and self.end_ts and self.start_ts > self.end_ts:
                raise ValueError("开始日期不能晚于结束日期")
        except ValueError as exc:
            messagebox.showerror("日期错误", str(exc), parent=self)
            return
        sel = list(self.msg_list.curselection())
        self.message_ids = ({self.messages[i].msg_id for i in sel} if sel else None)
        self._step2()

    def _step2(self) -> None:
        self._clear()
        ttk.Label(self, text="导出格式", font=("Microsoft YaHei UI", 11, "bold")).pack(
            anchor="w", padx=12, pady=(12, 4))
        for value, label in (("txt", "TXT（纯文本）"), ("pdf", "PDF（含图片）"),
                             ("html", "HTML（含文件链接）")):
            ttk.Radiobutton(self, text=label, value=value, variable=self.fmt_var).pack(
                anchor="w", padx=20, pady=2)
        ttk.Label(self, text="导出位置", font=("Microsoft YaHei UI", 11, "bold")).pack(
            anchor="w", padx=12, pady=(16, 4))
        row = ttk.Frame(self)
        row.pack(fill="x", padx=12)
        ttk.Entry(row, textvariable=self.out_var).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="选择…", command=self._browse).pack(side="left", padx=4)
        ttk.Label(self, text="将在该位置创建「QQ聊天记录导出」文件夹").pack(
            anchor="w", padx=12, pady=(2, 0))
        btns = ttk.Frame(self)
        btns.pack(fill="x", padx=12, pady=18)
        ttk.Button(btns, text="上一步", command=self._step1).pack(side="left")
        ttk.Button(btns, text="开始导出", command=self._do_export).pack(side="right")
        ttk.Button(btns, text="取消", command=self.destroy).pack(side="right", padx=6)

    def _browse(self) -> None:
        chosen = filedialog.askdirectory(parent=self, initialdir=self.out_var.get() or None)
        if chosen:
            self.out_var.set(chosen)

    def _do_export(self) -> None:
        out = Path(self.out_var.get().strip() or ".")
        self.destroy()
        self.on_export(self.info, self.message_ids, self.start_ts, self.end_ts,
                       self.fmt_var.get(), out)


class DateRangeDialog(tk.Toplevel):
    """小日历：第一次点选开始，第二次点选结束。"""

    def __init__(self, parent, start: int | None, end: int | None, *, on_ok):
        super().__init__(parent)
        self.on_ok = on_ok
        self.title("选择日期范围")
        self.transient(parent)
        self.grab_set()
        today = datetime.fromtimestamp(start) if start else datetime.now()
        self.year, self.month = today.year, today.month
        self.start = datetime.fromtimestamp(start).date() if start else None
        self.end = datetime.fromtimestamp(end).date() if end else None
        self.body = ttk.Frame(self, padding=10)
        self.body.pack(fill="both", expand=True)
        self._build()

    def _build(self) -> None:
        for child in self.body.winfo_children():
            child.destroy()
        head = ttk.Frame(self.body)
        head.pack(fill="x")
        ttk.Button(head, text="◀", width=3,
                   command=lambda: self._shift(-1)).pack(side="left")
        ttk.Label(head, text=f"{self.year} 年 {self.month} 月",
                  font=("Microsoft YaHei UI", 10, "bold")).pack(
            side="left", fill="x", expand=True)
        ttk.Button(head, text="▶", width=3,
                   command=lambda: self._shift(1)).pack(side="right")
        grid = ttk.Frame(self.body)
        grid.pack(pady=6)
        for i, name in enumerate(("一", "二", "三", "四", "五", "六", "日")):
            tk.Label(grid, text=name, width=4, fg="#888").grid(row=0, column=i)
        first = datetime(self.year, self.month, 1)
        offset = first.weekday()
        days = calendar.monthrange(self.year, self.month)[1]
        for day in range(1, days + 1):
            d = datetime(self.year, self.month, day).date()
            in_range = self.start is not None and (
                (self.end is None and d == self.start) or
                (self.end is not None and self.start <= d <= self.end))
            bg = "#2f7cf6" if in_range else "#ffffff"
            fg = "#ffffff" if in_range else "#222"
            btn = tk.Button(grid, text=str(day), width=4, bg=bg, fg=fg,
                            relief="flat", command=lambda d=d: self._pick(d))
            btn.grid(row=1 + (offset + day - 1) // 7,
                     column=(offset + day - 1) % 7, padx=1, pady=1)
        self.range_label = ttk.Label(self.body, text=self._range_text())
        self.range_label.pack(pady=(6, 0))
        btns = ttk.Frame(self.body)
        btns.pack(fill="x", pady=(8, 0))
        ttk.Button(btns, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(btns, text="确定", command=self._ok).pack(side="right", padx=6)

    def _range_text(self) -> str:
        if self.start and self.end:
            return (f"已选：{self.start:%Y-%m-%d} 至 {self.end:%Y-%m-%d}")
        if self.start:
            return f"已选开始：{self.start:%Y-%m-%d}，再点一天选结束"
        return "点击日期选择开始"

    def _shift(self, delta: int) -> None:
        month = self.month + delta
        year = self.year
        if month < 1:
            month, year = 12, year - 1
        elif month > 12:
            month, year = 1, year + 1
        self.year, self.month = year, month
        self._build()

    def _pick(self, day) -> None:
        if self.start is None or self.end is not None:
            self.start, self.end = day, None
        elif day < self.start:
            self.end, self.start = self.start, day
        else:
            self.end = day
        self._build()

    def _ok(self) -> None:
        start = (int(datetime.combine(self.start, datetime.min.time()).timestamp())
                 if self.start else None)
        end = (int(datetime.combine(self.end, datetime.max.time()).timestamp())
               if self.end else None)
        self.destroy()
        self.on_ok(start, end)


class FormatDialog(tk.Toplevel):
    def __init__(self, parent, default_out: str, *, on_ok):
        super().__init__(parent)
        self.on_ok = on_ok
        self.title("选择格式和位置")
        self.transient(parent)
        self.grab_set()
        body = ttk.Frame(self, padding=14)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="导出格式（可多选）",
                  font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w")
        self.vars = {"txt": tk.BooleanVar(value=False),
                     "pdf": tk.BooleanVar(value=False),
                     "html": tk.BooleanVar(value=True)}
        labels = {"txt": "TXT（纯文本）", "pdf": "PDF（含图片）",
                  "html": "HTML（含文件链接）"}
        for key in ("txt", "pdf", "html"):
            ttk.Checkbutton(body, text=labels[key],
                            variable=self.vars[key]).pack(anchor="w", padx=16)
        ttk.Label(body, text="导出位置", font=("Microsoft YaHei UI", 10, "bold")).pack(
            anchor="w", pady=(14, 4))
        row = ttk.Frame(body)
        row.pack(fill="x")
        self.out_var = tk.StringVar(value=default_out)
        ttk.Entry(row, textvariable=self.out_var).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="选择…", command=self._browse).pack(side="left", padx=4)
        btns = ttk.Frame(body)
        btns.pack(fill="x", pady=(16, 0))
        ttk.Button(btns, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(btns, text="确定", command=self._ok).pack(side="right", padx=6)

    def _browse(self) -> None:
        chosen = filedialog.askdirectory(parent=self, initialdir=self.out_var.get() or None)
        if chosen:
            self.out_var.set(chosen)

    def _ok(self) -> None:
        formats = {k for k, v in self.vars.items() if v.get()}
        if not formats:
            messagebox.showinfo("请选择格式", "至少选择一种导出格式。", parent=self)
            return
        self.destroy()
        self.on_ok(formats, Path(self.out_var.get().strip() or "."))


class ImageViewer(tk.Toplevel):
    """软件内图片查看器；点聊天图片打开，可再交给系统查看原图。"""

    def __init__(self, parent, path: Path | None = None,
                 data_uri: str | None = None):
        super().__init__(parent)
        self.path = path
        self.title(path.name if path else "图片")
        self.transient(parent)
        try:
            if data_uri:
                raw = base64.b64decode(data_uri.split(",", 1)[1])
                img = Image.open(io.BytesIO(raw))
            elif path is not None:
                img = Image.open(path)
            else:
                raise ValueError("没有图片数据")
            max_w = int(self.winfo_screenwidth() * 0.8)
            max_h = int(self.winfo_screenheight() * 0.75)
            img.thumbnail((max_w, max_h))
            self.photo = ImageTk.PhotoImage(img)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("无法打开图片", str(exc), parent=self)
            self.destroy()
            return
        tk.Label(self, image=self.photo, bg="#222").pack(fill="both", expand=True)
        btns = ttk.Frame(self, padding=6)
        btns.pack(fill="x")
        if path is not None:
            ttk.Button(btns, text="用系统查看器打开",
                       command=lambda: os.startfile(path)).pack(side="left")
        ttk.Button(btns, text="关闭", command=self.destroy).pack(side="right")


def run_gui(*, data_dir: Path | None = None, uin: str | None = None) -> int:
    root = tk.Tk()
    app = App(root, data_dir=data_dir, uin=uin)
    root.protocol("WM_DELETE_WINDOW", app.close)
    root.mainloop()
    return 0
