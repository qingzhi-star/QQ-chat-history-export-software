"""导出编排：单格式、固定大目录 `QQ聊天记录导出`、子目录 `名称-账号`。

- txt：只写 chat.txt（无媒体、无报告、无 contacts）
- html：chat.html + media/（只含被引用媒体）
- pdf：只写 chat.pdf（图片由 Edge 嵌入，临时 html/media 用完即删）
"""
from __future__ import annotations

import base64
import mimetypes
import os
import re
import urllib.request
import re
import shutil
import time
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path

from qqexport.export import html as html_mod
from qqexport.export import media_copy, pdf as pdf_mod, txt as txt_mod
from qqexport.qq import avatars
from qqexport.export.model import Chat, MediaRef, Message, Segment
from qqexport.export.report import ExportReport
from qqexport.export.session import ChatInfo, ExportSource

ROOT_FOLDER = "QQ聊天记录导出"
_FORMATS = {"txt", "html", "pdf"}
_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"con", "prn", "aux", "nul"} | {f"com{i}" for i in range(1, 10)} | \
            {f"lpt{i}" for i in range(1, 10)}


@dataclass
class ExportOptions:
    format: str = "html"
    formats: set[str] | None = None
    out_base: Path = field(default_factory=lambda: Path.cwd())
    now: datetime | None = None
    encoding: str = "utf-8"
    allow_large_pdf: bool = False
    edge: Path | None = None
    start_ts: int | None = None
    end_ts: int | None = None
    message_ids: set[int] | None = None


def folder_name(info: ChatInfo) -> str:
    title = info.title
    suffix = f"（{info.account}）"
    if info.account and title.endswith(suffix):
        title = title[:-len(suffix)]
    raw = f"{title}-{info.account}"
    cleaned = _INVALID.sub("_", raw).strip().rstrip(". ")
    if not cleaned:
        cleaned = f"{info.kind}-{info.account}"
    if cleaned.lower() in _RESERVED:
        cleaned = "_" + cleaned
    return cleaned


_MD_IMAGE = re.compile(r"!\[([^\]]*)\]\((https?://[^)]+)\)")


def _fetch_image(url: str, cache: dict[str, str | None]) -> str | None:
    if url in cache:
        return cache[url]
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0 qqexport/0.1"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = resp.read(4 * 1024 * 1024)
        if not (data.startswith(b"\xff\xd8") or data.startswith(b"\x89PNG")
                or data.startswith(b"GIF8")):
            cache[url] = None
            return None
        mime = "image/png" if data.startswith(b"\x89PNG") else (
            "image/gif" if data.startswith(b"GIF8") else "image/jpeg")
        uri = f"data:{mime};base64," + base64.b64encode(data).decode("ascii")
    except Exception:
        uri = None
    cache[url] = uri
    return uri


def _expand_markdown_images(messages: list[Message], *,
                            fetch: bool) -> list[Message]:
    """机器人卡片常把图片放在 markdown 里；抽成真正的 image 段。"""
    cache: dict[str, str | None] = {}
    out: list[Message] = []
    for msg in messages:
        segs: list[Segment] = []
        for seg in msg.segments:
            if seg.kind != "markdown" or not seg.text:
                segs.append(seg)
                continue
            text = seg.text
            images: list[Segment] = []
            for alt, url in _MD_IMAGE.findall(text):
                text = text.replace(f"![{alt}]({url})", "[图片]")
                if not fetch:
                    continue
                uri = _fetch_image(url, cache)
                if uri:
                    images.append(Segment(kind="image", media=MediaRef(
                        kind="image", missing=False, data_uri=uri,
                        filename=alt or None)))
            segs.append(replace(seg, text=text))
            segs.extend(images)
        out.append(replace(msg, segments=segs))
    return out


def _relink(messages: list[Message], rel: dict[Path, str]) -> list[Message]:
    out = []
    for msg in messages:
        segs = []
        for seg in msg.segments:
            if seg.media is not None and seg.media.source in rel:
                segs.append(replace(seg, media=replace(
                    seg.media, rel_path=f"media/{rel[seg.media.source]}")))
            else:
                segs.append(seg)
        out.append(replace(msg, segments=segs))
    return out


def _fetch_remote_image(url: str,
                        cache: dict[str, str | None]) -> str | None:
    if url in cache:
        return cache[url]
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "QQ/9.9.36", "Referer": "https://im.qq.com/",
            "Accept": "*/*"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = resp.read(8 * 1024 * 1024)
        if not (data.startswith(b"\xff\xd8") or data.startswith(b"\x89PNG")
                or data.startswith(b"GIF8")):
            cache[url] = None
            return None
        mime = ("image/png" if data.startswith(b"\x89PNG") else
                "image/gif" if data.startswith(b"GIF8") else "image/jpeg")
        uri = f"data:{mime};base64," + base64.b64encode(data).decode("ascii")
    except Exception:
        uri = None
    cache[url] = uri
    return uri


def _inline_images(messages: list[Message]) -> list[Message]:
    cache: dict[Path, str | None] = {}
    remote_cache: dict[str, str | None] = {}
    out: list[Message] = []
    for msg in messages:
        segs = []
        for seg in msg.segments:
            m = seg.media
            if (seg.kind == "image" and m is not None
                    and not m.data_uri):
                uri = None
                if m.source is not None and not m.missing:
                    key = m.source
                    if key not in cache:
                        try:
                            data = key.read_bytes()
                            mime = mimetypes.guess_type(str(key))[0] or "image/jpeg"
                            cache[key] = (f"data:{mime};base64,"
                                          + base64.b64encode(data).decode("ascii"))
                        except OSError:
                            cache[key] = None
                    uri = cache[key]
                elif m.remote_url:
                    uri = _fetch_remote_image(m.remote_url, remote_cache)
                if uri:
                    segs.append(replace(seg, media=replace(
                        m, data_uri=uri, rel_path=None, missing=False)))
                    continue
            segs.append(seg)
        out.append(replace(msg, segments=segs))
    return out


def export_chats(source: ExportSource, chat_ids: list[str],
                 opts: ExportOptions) -> ExportReport:
    formats = {f.lower() for f in (opts.formats or {opts.format})}
    if not formats or formats - _FORMATS:
        raise ValueError(f"不支持的格式：{formats}")
    started = time.monotonic()
    now = opts.now or datetime.now()
    root = opts.out_base / ROOT_FOLDER
    root.mkdir(parents=True, exist_ok=True)

    infos = {i.chat_id: i for i in source.chats()}
    report = ExportReport(
        started_at=now.isoformat(timespec="seconds"), uin=source.account_uin,
        chats_requested=len(chat_ids), warnings=list(source.warnings))
    dec = source.meta.get("decrypt")
    if dec is not None:
        report.page_count = getattr(dec, "page_count", 0)
        report.corrupt_pages = getattr(dec, "corrupt_pages", 0)
        report.wal_frames = getattr(dec, "wal_frames", 0)
        report.wal_last_commit_frame = getattr(dec, "wal_last_commit_frame", 0)
        report.db_size_bytes = source.msg_db.stat().st_size

    for chat_id in chat_ids:
        info = infos.get(chat_id)
        if info is None:
            report.warnings.append(f"未找到会话 {chat_id}")
            continue
        messages = list(source.iter_messages(chat_id))
        messages = [m for m in messages if not m.is_system]
        messages = _expand_markdown_images(
            messages, fetch=bool(formats & {"html", "pdf"}))
        if opts.message_ids is not None:
            wanted = set(opts.message_ids)
            messages = [m for m in messages if m.msg_id in wanted]
        if opts.start_ts is not None:
            messages = [m for m in messages if m.ts >= opts.start_ts]
        if opts.end_ts is not None:
            messages = [m for m in messages if m.ts <= opts.end_ts]
        refs = [s.media for m in messages for s in m.segments if s.media is not None]
        final = root / folder_name(info)
        if final.exists():
            shutil.rmtree(final)
        try:
            if formats == {"txt"}:
                final.mkdir(parents=True)
                chat = Chat(kind=info.kind, peer_id=info.peer, title=info.title,
                            subtitle=info.subtitle, messages=messages)
                txt_mod.write_txt(chat, final / "chat.txt",
                                  txt_mod.TxtOptions(encoding=opts.encoding))
            else:
                keep_html = "html" in formats
                work = final if keep_html else root / f".tmp-{os.getpid()}-{time.time_ns()}"
                work.mkdir(parents=True)
                rel: dict[Path, str] = {}
                report.media_referenced += len(refs)
                report.media_found += sum(
                    1 for r in refs if r.source is not None and not r.missing)
                report.media_missing += sum(
                    1 for r in refs if r.source is None or r.missing)
                report.media_bytes += sum(
                    (r.size if r.size is not None else r.source.stat().st_size)
                    for r in refs if r.source is not None and not r.missing
                    and r.source.is_file())
                file_refs = ([r for r in refs if r.kind == "file"]
                             if keep_html else [])
                if file_refs:
                    copied = media_copy.copy_media(file_refs, work / "media")
                    rel = copied.rel_by_source
                relinked = _inline_images(_relink(messages, rel))
                if not source.meta.get("synthetic"):
                    relinked = avatars.add_avatars(relinked)
                nt_data = Path(source.meta.get("nt_data") or
                               avatars.nt_data_dir(source.msg_db))
                emoji = avatars.add_emoji(relinked, nt_data)
                chat = Chat(kind=info.kind, peer_id=info.peer, title=info.title,
                            subtitle=info.subtitle, messages=relinked)
                html_mod.write_html(chat, work, html_mod.HtmlOptions(
                    now=now, account_uin=source.account_uin,
                    shard_size=10 ** 9, emoji_map=emoji,
                    link_files=keep_html))
                if "pdf" in formats:
                    if len(messages) > 30000 and not opts.allow_large_pdf:
                        report.pdf_error = "消息超过 30000 条，未生成 PDF"
                        report.warnings.append(f"{chat_id}: {report.pdf_error}")
                    else:
                        res = pdf_mod.write_pdf(work / "chat.html",
                                                work / "chat.pdf", edge=opts.edge)
                        if not res.ok:
                            report.pdf_error = res.error
                            report.warnings.append(f"{chat_id}: {res.error}")
                        elif not keep_html:
                            final.mkdir(parents=True, exist_ok=True)
                            shutil.move(str(work / "chat.pdf"),
                                        str(final / "chat.pdf"))
                if "txt" in formats:
                    if not keep_html:
                        final.mkdir(parents=True, exist_ok=True)
                    txt_mod.write_txt(chat, final / "chat.txt",
                                      txt_mod.TxtOptions(encoding=opts.encoding))
                if not keep_html:
                    shutil.rmtree(work, ignore_errors=True)
                    if not final.exists() or not any(final.iterdir()):
                        shutil.rmtree(final, ignore_errors=True)

            report.exported_dirs.append(str(final))
            report.chats_exported += 1
            for m in messages:
                report.messages_total += 1
                bad = not m.segments or any(s.kind == "unknown" for s in m.segments)
                report.messages_degraded += 1 if bad else 0
                report.messages_ok += 0 if bad else 1
                for s in m.segments:
                    report.segments_by_kind[s.kind] = \
                        report.segments_by_kind.get(s.kind, 0) + 1
                    if s.unknown_field:
                        report.unknown_fields[s.unknown_field] = \
                            report.unknown_fields.get(s.unknown_field, 0) + 1
                    if s.kind == "forward_nt":
                        report.forwards_nt_unexpanded += 1
                    if s.kind == "forward_legacy":
                        report.forwards_legacy_rendered += 1
                    if s.kind == "voice":
                        report.voice_raw += 1
        except Exception as exc:
            shutil.rmtree(final, ignore_errors=True)
            report.warnings.append(f"{chat_id}: {type(exc).__name__}")

    report.messages_failed = max(0, report.messages_total - report.messages_ok
                                 - report.messages_degraded)
    report.finished_at = datetime.now().isoformat(timespec="seconds")
    report.duration_s = round(time.monotonic() - started, 2)
    return report
