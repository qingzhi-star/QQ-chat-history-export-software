"""T8：txt 导出（UTF-8 BOM / GBK 回退 / 名称：内容 格式）。"""
from __future__ import annotations

import codecs
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from qqexport.export.model import Chat, MediaRef, Message, Segment

_WEEK = "一二三四五六日"


@dataclass
class TxtOptions:
    encoding: str = "utf-8"
    bom: bool = True
    now: datetime | None = None
    account_uin: str = ""


@dataclass
class TxtResult:
    path: Path
    messages: int
    replacements: int = 0


def _size(n: int | None) -> str:
    if n is None:
        return ""
    if n >= 1024 * 1024:
        return f"{n / 1024 / 1024:.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n} B"


def _media_text(seg: Segment) -> str:
    m = seg.media
    if m is None:
        return ""
    if m.missing or not m.rel_path:
        return {"image": "[图片 · 本地文件缺失]", "video": "[视频 · 本地文件缺失]",
                "voice": "[语音 · 本地文件缺失]", "file": "[文件 · 本地文件缺失]"}.get(
                    seg.kind, "[媒体缺失]")
    if seg.kind == "image":
        return f"[图片] {m.rel_path}"
    if seg.kind == "video":
        return f"[视频] {m.rel_path}"
    if seg.kind == "voice":
        sec = f"{m.duration_ms // 1000}″" if m.duration_ms else ""
        return f"[语音 {sec}] {m.rel_path}".replace(" ]", "]")
    if seg.kind == "file":
        name = m.filename or Path(m.rel_path).name
        return f"[文件] {name} ({_size(m.size)}) {m.rel_path}"
    return f"[媒体] {m.rel_path}"


def segment_text(seg: Segment) -> str:
    if seg.kind == "text":
        return seg.text or ""
    if seg.kind in ("image", "video", "voice", "file"):
        return _media_text(seg)
    if seg.kind == "face":
        return "[表情]"
    if seg.kind == "market_face":
        return "[商城表情]"
    if seg.kind == "gray_tip":
        return seg.text or ""
    if seg.kind == "red_bag":
        return "[红包]"
    if seg.kind == "markdown":
        return seg.text or ""
    if seg.kind == "reply":
        if seg.quoted is not None:
            quote = segment_text(seg.quoted.segments[0]) if seg.quoted.segments else ""
            return f"[回复 {seg.quoted.sender_name}：{quote}] {seg.text or ''}".rstrip()
        return f"[回复] {seg.text or ''}".rstrip()
    if seg.kind == "forward_nt":
        meta = seg.forward_meta or {}
        count = meta.get("count") or ""
        return f"[合并转发 · 本地无缓存，未展开] {count} 条消息".replace(" 条", " 条")
    if seg.kind == "forward_legacy":
        return "[老式转发]"
    if seg.kind == "poke":
        return seg.text or "[戳一戳]"
    if seg.kind == "call":
        return seg.text or "[通话]"
    if seg.kind == "ark":
        return "[卡片]"
    if seg.kind == "live":
        return "[直播]"
    if seg.kind == "unknown":
        return f"[未解析 字段 {seg.unknown_field}]" if seg.unknown_field else "[未解析]"
    return f"[{seg.kind}]"


def _message_line(msg: Message) -> str:
    body = " ".join(segment_text(s) for s in msg.segments).strip()
    extra = []
    for s in msg.segments:
        if s.kind == "forward_legacy" and s.forward_previews:
            extra = [f"    {name}：{text}" for name, text in s.forward_previews]
    stamp = datetime.fromtimestamp(msg.ts).strftime("%H:%M:%S")
    prefix = "" if msg.is_self else ""
    return "\n".join([f"[{stamp}] {msg.sender_name}：{body}" + prefix] + extra)


def write_txt(chat: Chat, out: Path, opts: TxtOptions) -> TxtResult:
    out.parent.mkdir(parents=True, exist_ok=True)
    now = opts.now or datetime.now()
    messages = sorted(chat.messages, key=lambda m: (m.ts, m.msg_id))
    lines: list[str] = []
    lines.append("=" * 60)
    kind = "群聊" if chat.kind == "group" else "私聊"
    subtitle = f"  ({chat.subtitle})" if chat.subtitle else ""
    lines.append(f"{kind}：{chat.title}{subtitle}")
    lines.append(f"导出账号：{opts.account_uin}")
    lines.append(f"导出时间：{now:%Y-%m-%d %H:%M:%S}")
    if messages:
        first = datetime.fromtimestamp(messages[0].ts).strftime("%Y-%m-%d")
        last = datetime.fromtimestamp(messages[-1].ts).strftime("%Y-%m-%d")
        lines.append(f"时间范围：{first} ~ {last}")
    lines.append(f"消息条数：{len(messages)}")
    lines.append("=" * 60)
    lines.append("")

    enc = opts.encoding.lower()
    use_bom = opts.bom and enc in ("utf-8", "utf8")
    replacements = 0
    with open(out, "wb") as f:
        if use_bom:
            f.write(codecs.BOM_UTF8)
        for line in lines:
            f.write(_encode(line + "\n", enc))
        current_day = None
        for msg in messages:
            day = datetime.fromtimestamp(msg.ts).date()
            if day != current_day:
                current_day = day
                weekday = _WEEK[day.weekday()]
                for line in ("", f"---------- {day:%Y-%m-%d} 星期{weekday} ----------", ""):
                    f.write(_encode(line + "\n", enc))
            for line in (_message_line(msg).splitlines() or [""]):
                data, repl = _encode_count(line + "\n", enc)
                replacements += repl
                f.write(data)
    return TxtResult(out, len(messages), replacements)


def _encode(line: str, enc: str) -> bytes:
    return _encode_count(line, enc)[0]


def _encode_count(line: str, enc: str) -> tuple[bytes, int]:
    if enc in ("utf-8", "utf8"):
        return line.encode("utf-8"), 0
    data = line.encode(enc, "replace")
    replaced = data.decode(enc, "replace").count("?") - line.count("?")
    return data, max(0, replaced)
