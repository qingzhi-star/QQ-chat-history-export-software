"""P1 txt 导出：纯文本逐行 `[时间 用户名称]：内容`。

不写头部、不写日期分隔、不复制媒体；图片为 `[图片]`，文件为 `[文件] 名称`。
"""
from __future__ import annotations

import codecs
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from qqexport.export.model import Chat, Message, Segment


@dataclass
class TxtOptions:
    encoding: str = "utf-8"
    bom: bool = True


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


_MD_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]+\)")
_MD_HEAD = re.compile(r"^\s{0,3}#{1,6}\s*", re.MULTILINE)
_MD_QUOTE = re.compile(r"^\s*>\s?", re.MULTILINE)


def clean_markdown(text: str | None) -> str:
    if not text:
        return ""
    out = _MD_LINK.sub(lambda m: m.group(1) or "[图片]", text)
    out = _MD_HEAD.sub("", out)
    out = _MD_QUOTE.sub("", out)
    out = out.replace("**", "").replace("`", "")
    return out.strip()


def segment_text(seg: Segment) -> str:
    if seg.kind == "text":
        return seg.text or ""
    if seg.kind == "image":
        return "[图片]"
    if seg.kind == "file":
        return "[文件]"
    if seg.kind == "video":
        return "[视频]"
    if seg.kind == "voice":
        return "[语音]"
    if seg.kind == "face":
        return "[表情]"
    if seg.kind == "market_face":
        return "[商城表情]"
    if seg.kind == "gray_tip":
        return seg.text or ""
    if seg.kind == "red_bag":
        return "[红包]"
    if seg.kind == "markdown":
        return clean_markdown(seg.text)
    if seg.kind == "reply":
        if seg.quoted is not None and seg.quoted.segments:
            quote = " ".join(segment_text(s) for s in seg.quoted.segments).strip()
            return f"[回复 {seg.quoted.sender_name}：{quote}] {seg.text or ''}".rstrip()
        return f"[回复] {seg.text or ''}".rstrip()
    if seg.kind == "forward_nt":
        return "[合并转发]"
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
        return "[未解析]"
    return f"[{seg.kind}]"


def _line(msg: Message) -> str:
    body = " ".join(segment_text(s) for s in msg.segments).strip()
    stamp = datetime.fromtimestamp(msg.ts).strftime("%Y-%m-%d %H:%M:%S")
    return f"[{stamp} {msg.sender_name}]：{body}"


def format_line(msg: Message) -> str:
    """供 GUI 预览复用与 txt 完全一致的行格式。"""
    return _line(msg)


def write_txt(chat: Chat, out: Path, opts: TxtOptions) -> TxtResult:
    out.parent.mkdir(parents=True, exist_ok=True)
    messages = sorted(chat.messages, key=lambda m: (m.ts, m.msg_id))
    enc = opts.encoding.lower()
    use_bom = opts.bom and enc in ("utf-8", "utf8")
    replacements = 0
    with open(out, "wb") as f:
        if use_bom:
            f.write(codecs.BOM_UTF8)
        for msg in messages:
            data, repl = _encode_count(_line(msg) + "\n", enc)
            replacements += repl
            f.write(data)
    return TxtResult(out, len(messages), replacements)


def _encode_count(line: str, enc: str) -> tuple[bytes, int]:
    if enc in ("utf-8", "utf8"):
        return line.encode("utf-8"), 0
    data = line.encode(enc, "replace")
    replaced = data.decode(enc, "replace").count("?") - line.count("?")
    return data, max(0, replaced)
