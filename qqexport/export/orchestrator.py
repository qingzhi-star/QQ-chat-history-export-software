"""T11：导出编排（txt/html/pdf + 媒体 + 报告 + 原子目录）。"""
from __future__ import annotations

import os
import re
import shutil
import time
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path

from qqexport.export import html as html_mod
from qqexport.export import media_copy, pdf as pdf_mod, txt as txt_mod
from qqexport.export.model import Chat, Message, Segment
from qqexport.export.report import ExportReport
from qqexport.export.session import ChatInfo, ExportSource

_RESERVED = {"con", "prn", "aux", "nul"} | {f"com{i}" for i in range(1, 10)} | \
            {f"lpt{i}" for i in range(1, 10)}


@dataclass
class ExportOptions:
    formats: set[str] = field(default_factory=lambda: {"html"})
    out_dir: Path = field(default_factory=lambda: Path("export"))
    now: datetime | None = None
    encoding: str = "utf-8"
    shard_size: int = 20000
    no_media: bool = False
    allow_large_pdf: bool = False
    edge: Path | None = None


def sanitize_component(text: str | None, fallback: str = "chat") -> str:
    value = (text or "").strip()
    value = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", value)
    value = value.strip("._-")[:40] or fallback
    if value.lower() in _RESERVED:
        value = "_" + value
    return value


def chat_slug(info: ChatInfo) -> str:
    if info.kind == "group":
        name = sanitize_component(info.title, info.peer)
        return sanitize_component(f"group_{info.peer}_{name[:12]}", info.peer)
    return sanitize_component(f"c2c_{sanitize_component(info.title, info.peer)[:12]}",
                              info.peer)


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


def export_chats(source: ExportSource, chat_ids: list[str],
                 opts: ExportOptions) -> ExportReport:
    started = time.monotonic()
    now = opts.now or datetime.now()
    formats = set(opts.formats)
    if "pdf" in formats:
        formats.add("html")
    out_root = opts.out_dir.resolve()
    account_dir = out_root / source.account_uin
    account_dir.mkdir(parents=True, exist_ok=True)

    infos = {i.chat_id: i for i in source.chats()}
    dec = source.meta.get("decrypt")
    report = ExportReport(
        started_at=now.isoformat(timespec="seconds"), uin=source.account_uin,
        qq_version=source.meta.get("qq_version", ""),
        db_size_bytes=source.msg_db.stat().st_size,
        page_count=getattr(dec, "page_count", 0),
        corrupt_pages=getattr(dec, "corrupt_pages", 0),
        wal_frames=getattr(dec, "wal_frames", 0),
        wal_last_commit_frame=getattr(dec, "wal_last_commit_frame", 0),
        wal_truncated=getattr(dec, "wal_truncated", False),
        chats_requested=len(chat_ids), warnings=list(source.warnings))
    if dec is not None:
        report.cipher_params = tuple(dec.cipher_fingerprint.split("/"))  # type: ignore
    seg_counts: Counter[str] = Counter()
    unknown: Counter[int] = Counter()

    for chat_id in chat_ids:
        info = infos.get(chat_id)
        if info is None:
            report.warnings.append(f"未找到会话 {chat_id}")
            continue
        messages = list(source.iter_messages(chat_id))
        used = set()
        refs = []
        for m in messages:
            used.add((info.peer if info.kind == "group" else None,
                      m.sender_uid, m.sender_uin))
            for s in m.segments:
                if s.media is not None:
                    refs.append(s.media)
        slug = chat_slug(info)
        final = account_dir / slug
        tmp = account_dir / f".{slug}.partial-{os.getpid()}"
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        try:
            rel: dict[Path, str] = {}
            if refs and not opts.no_media:
                copied = media_copy.copy_media(refs, tmp / "media")
                rel = copied.rel_by_source
                report.media_found += copied.copied
                report.media_missing += copied.missing
                report.media_bytes += copied.bytes_copied
            report.media_referenced += len(refs)
            messages = _relink(messages, rel)
            snapshot = source.contacts.snapshot(used)
            (tmp / "contacts.json").write_text(
                __import__("json").dumps(snapshot, ensure_ascii=False, indent=2),
                encoding="utf-8")

            chat = Chat(kind=info.kind, peer_id=info.peer, title=info.title,
                        subtitle=info.subtitle, messages=messages)
            for m in messages:
                report.messages_total += 1
                bad = not m.segments or any(s.kind == "unknown" for s in m.segments)
                report.messages_degraded += 1 if bad else 0
                report.messages_ok += 0 if bad else 1
                for s in m.segments:
                    seg_counts[s.kind] += 1
                    if s.unknown_field:
                        unknown[s.unknown_field] += 1
                    if s.kind == "forward_nt":
                        report.forwards_nt_unexpanded += 1
                    if s.kind == "forward_legacy":
                        report.forwards_legacy_rendered += 1
                    if s.kind == "voice":
                        report.voice_raw += 1

            if "txt" in formats:
                txt_mod.write_txt(chat, tmp / "chat.txt", txt_mod.TxtOptions(
                    encoding=opts.encoding, now=now,
                    account_uin=source.account_uin))
            if "html" in formats:
                html_mod.write_html(chat, tmp, html_mod.HtmlOptions(
                    now=now, account_uin=source.account_uin,
                    shard_size=opts.shard_size))
            if "pdf" in formats:
                if len(messages) > 30000 and not opts.allow_large_pdf:
                    report.pdf_error = "消息超过 30000 条，未生成 PDF（需 --allow-large-pdf）"
                else:
                    res = pdf_mod.write_pdf(tmp / "chat.html", tmp / "chat.pdf",
                                            edge=opts.edge)
                    if not res.ok:
                        report.pdf_error = res.error
                        report.warnings.append(f"{chat_id}: {res.error}")

            if final.exists():
                backup = final.with_name(f"{final.name}.bak-{now:%Y%m%d-%H%M%S}")
                final.rename(backup)
            os.replace(tmp, final)
            report.chats_exported += 1
        except Exception as exc:
            shutil.rmtree(tmp, ignore_errors=True)
            report.warnings.append(f"{chat_id}: {type(exc).__name__}")
            report.pdf_error = report.pdf_error or None

    report.segments_by_kind = dict(seg_counts)
    report.unknown_fields = dict(unknown)
    report.messages_failed = max(0, report.messages_total
                                 - report.messages_ok - report.messages_degraded)
    report.finished_at = datetime.now().isoformat(timespec="seconds")
    report.duration_s = round(time.monotonic() - started, 2)
    report.write(out_root)
    _write_index(infos, out_root, account_dir, formats)
    return report


def _write_index(infos: dict[str, ChatInfo], out_root: Path,
                 account_dir: Path, formats: set[str]) -> None:
    rows = []
    for info in infos.values():
        slug = chat_slug(info)
        links = []
        if "txt" in formats:
            links.append(f'<a href="{slug}/chat.txt">txt</a>')
        if "html" in formats:
            links.append(f'<a href="{slug}/chat.html">html</a>')
        if "pdf" in formats:
            links.append(f'<a href="{slug}/chat.pdf">pdf</a>')
        rows.append(f'<li>{info.title}（{info.messages} 条） {" ".join(links)}</li>')
    (out_root / "index.html").write_text(
        "<!DOCTYPE html><meta charset=\"utf-8\"><title>qqexport</title>"
        f"<h1>导出索引</h1><ul>{''.join(rows)}</ul>", encoding="utf-8")
