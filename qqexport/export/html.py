"""T9：自包含 HTML 导出（零外部资源，可按年份/数量分片）。"""
from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from qqexport.export.model import Chat, Message, Segment
from qqexport.export.txt import _size

_CSS = """
:root{--bg:#f5f6f8;--card:#fff;--self:#2f6fed;--self-fg:#fff;--muted:#888}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);font:15px/1.6 "Microsoft YaHei","PingFang SC",sans-serif;color:#222}
header{position:sticky;top:0;z-index:5;background:#fff;border-bottom:1px solid #e3e6ea;padding:10px 16px}
header h1{font-size:17px;margin:0}.meta{color:var(--muted);font-size:12px}
#wrap{max-width:900px;margin:0 auto;padding:12px 16px 60px}
.day{position:sticky;top:56px;text-align:center;margin:18px 0 8px}
.day span{background:#dfe4ea;color:#555;border-radius:12px;padding:3px 12px;font-size:12px}
.msg{display:flex;gap:8px;margin:10px 0;align-items:flex-start}
.msg.self{flex-direction:row-reverse}
.avatar{width:34px;height:34px;border-radius:50%;background:#9aa7b8;color:#fff;flex:0 0 34px;
 display:flex;align-items:center;justify-content:center;font-size:14px}
.bubble{max-width:72%;background:var(--card);border-radius:10px;padding:8px 12px;box-shadow:0 1px 2px #0001}
.self .bubble{background:var(--self);color:var(--self-fg)}
.bubble .who{font-size:12px;color:var(--muted);margin-bottom:2px}
.self .bubble .who{color:#dfe8ff}
.text{white-space:pre-wrap;word-break:break-word}
img.media{max-width:100%;border-radius:8px;display:block;margin:4px 0;cursor:zoom-in}
video{max-width:100%;border-radius:8px}
blockquote{border-left:3px solid #c9d2df;margin:4px 0;padding:2px 8px;color:#666;font-size:13px}
.self blockquote{border-color:#bcd0ff;color:#e8efff}
details{margin:4px 0;font-size:13px}
.unknown{background:#fff6d6;border:1px solid #e8d48a;border-radius:8px;padding:6px 8px}
.gray{text-align:center;color:#999;font-size:12px;margin:8px 0}
#lightbox{position:fixed;inset:0;background:#000c;display:none;align-items:center;justify-content:center;z-index:20}
#lightbox.show{display:flex}#lightbox img{max-width:94%;max-height:94%}
footer{text-align:center;color:var(--muted);font-size:12px;margin:24px}
@media print{@page{size:A4;margin:18mm 15mm}body{background:#fff}
 .msg,.day{break-inside:avoid}header{position:static}.no-print{display:none}
 #lightbox{display:none!important}.bubble{box-shadow:none;border:1px solid #ddd}
 .self .bubble{background:#eaf1ff;color:#000}.self .bubble .who{color:#666}}
"""

_JS = """
document.querySelectorAll('img.media').forEach(function(img){
  img.addEventListener('click',function(){
    var lb=document.getElementById('lightbox');var big=document.getElementById('big');
    big.src=img.src;lb.classList.add('show');});
});
document.getElementById('lightbox').addEventListener('click',function(){
  this.classList.remove('show');});
"""


@dataclass
class HtmlOptions:
    now: datetime | None = None
    account_uin: str = ""
    shard_size: int = 20000


@dataclass
class HtmlResult:
    files: list[Path]
    messages: int
    shards: int


def _e(value: object | None) -> str:
    return html.escape(str(value)) if value is not None else ""


def _seg_html(seg: Segment) -> str:
    if seg.kind == "text":
        return f'<div class="text">{_e(seg.text)}</div>'
    if seg.kind in ("image", "video", "voice", "file"):
        m = seg.media
        if m is None or m.missing or not m.rel_path:
            return f'<div class="text">[{_e(seg.kind)} · 本地文件缺失]</div>'
        src = _e(m.rel_path)
        if seg.kind == "image":
            return (f'<a href="{src}" class="no-print"><img class="media" loading="lazy" '
                    f'src="{src}" alt="图片"></a>')
        if seg.kind == "video":
            return f'<video controls preload="metadata" src="{src}"></video>'
        if seg.kind == "voice":
            sec = f"{m.duration_ms // 1000}″" if m.duration_ms else ""
            return (f'<div class="text">▶ 语音 {sec} · '
                    f'<a download href="{src}">下载</a></div>')
        name = _e(m.filename or Path(m.rel_path).name)
        return (f'<div class="text">📄 {name} ({_size(m.size)}) · '
                f'<a download href="{src}">下载</a></div>')
    if seg.kind == "reply":
        quote = ""
        if seg.quoted is not None and seg.quoted.segments:
            quote = (f'<blockquote>{_e(seg.quoted.sender_name)}：'
                     f'{_e(seg.quoted.segments[0].text or "")}</blockquote>')
        return quote + f'<div class="text">{_e(seg.text)}</div>'
    if seg.kind == "forward_legacy":
        items = "".join(f'<div>{_e(n)}：{_e(t)}</div>'
                        for n, t in (seg.forward_previews or []))
        return f'<details><summary>[老式转发]</summary>{items}</details>'
    if seg.kind == "forward_nt":
        meta = seg.forward_meta or {}
        return (f'<div class="unknown">[合并转发 · 本地无缓存，未展开] '
                f'{_e(meta.get("count", ""))} 条消息</div>')
    if seg.kind == "unknown":
        detail = f'<details><summary>[未解析 字段 {_e(seg.unknown_field)}]</summary>' \
                 f'<code>{_e(seg.raw_hex)}</code></details>'
        return f'<div class="unknown">{detail}</div>'
    if seg.kind == "gray_tip":
        return f'<div class="gray">{_e(seg.text)}</div>'
    label = {"face": "[表情]", "market_face": "[商城表情]", "red_bag": "[红包]",
             "markdown": None, "poke": None, "call": None, "ark": "[卡片]",
             "live": "[直播]"}.get(seg.kind, f"[{seg.kind}]")
    return f'<div class="text">{_e(seg.text if seg.text else label)}</div>'


def _msg_html(msg: Message) -> str:
    cls = "msg self" if msg.is_self else "msg other"
    avatar = _e((msg.sender_name or "?")[:1])
    stamp = datetime.fromtimestamp(msg.ts).strftime("%H:%M:%S")
    parts = "".join(_seg_html(s) for s in msg.segments)
    return (f'<div class="{cls}"><div class="avatar">{avatar}</div>'
            f'<div class="bubble"><div class="who">{_e(msg.sender_name)} · {stamp}</div>'
            f'{parts}</div></div>')


def _header_html(chat: Chat, messages: list[Message], now: datetime,
                 account: str) -> str:
    first = datetime.fromtimestamp(messages[0].ts).strftime("%Y-%m-%d") if messages else "-"
    last = datetime.fromtimestamp(messages[-1].ts).strftime("%Y-%m-%d") if messages else "-"
    sub = f" · {_e(chat.subtitle)}" if chat.subtitle else ""
    return (f'<header><h1>{_e(chat.title)}{sub}</h1>'
            f'<div class="meta">账号 {_e(account)} · {len(messages)} 条 · '
            f'{first} ~ {last} · 导出于 {now:%Y-%m-%d %H:%M:%S}</div></header>')


def _write_doc(path: Path, chat: Chat, messages: list[Message], now: datetime,
               account: str, nav: str = "") -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write("<!DOCTYPE html>\n<html lang=\"zh-CN\"><head><meta charset=\"utf-8\">")
        f.write(f"<title>{_e(chat.title)}</title><style>{_CSS}</style></head><body>")
        f.write(_header_html(chat, messages, now, account))
        if nav:
            f.write(f'<div id="wrap">{nav}')
        else:
            f.write('<div id="wrap">')
        current_day = None
        for msg in messages:
            day = datetime.fromtimestamp(msg.ts).date()
            if day != current_day:
                current_day = day
                f.write(f'<div class="day"><span>{day:%Y-%m-%d}</span></div>')
            f.write(_msg_html(msg))
        f.write('</div><footer class="no-print">qqexport · 本地离线导出</footer>')
        f.write('<div id="lightbox"><img id="big" alt=""></div>')
        f.write(f"<script>{_JS}</script></body></html>")
        f.flush()


def _shards(messages: list[Message], size: int) -> list[tuple[str, list[Message]]]:
    if len(messages) <= size:
        return [("chat", messages)]
    by_year: dict[int, list[Message]] = {}
    for m in messages:
        by_year.setdefault(datetime.fromtimestamp(m.ts).year, []).append(m)
    out: list[tuple[str, list[Message]]] = []
    for year in sorted(by_year):
        chunk = by_year[year]
        if len(chunk) <= size:
            out.append((f"chat-{year}", chunk))
        else:
            for i in range(0, len(chunk), size):
                out.append((f"chat-{year}-{i // size + 1}", chunk[i:i + size]))
    return out


def write_html(chat: Chat, out_dir: Path, opts: HtmlOptions) -> HtmlResult:
    out_dir.mkdir(parents=True, exist_ok=True)
    now = opts.now or datetime.now()
    messages = sorted(chat.messages, key=lambda m: (m.ts, m.msg_id))
    chunks = _shards(messages, max(1, opts.shard_size))
    files: list[Path] = []
    nav = ""
    if len(chunks) > 1:
        links = "".join(f'<a href="{name}.html">{name}</a> ' for name, _ in chunks)
        nav = f'<div class="no-print">分片：{links}</div>'
    for name, chunk in chunks:
        path = out_dir / f"{name}.html"
        _write_doc(path, chat, chunk, now, opts.account_uin, nav if len(chunks) > 1 else "")
        files.append(path)
    if len(chunks) > 1:
        index = out_dir / "index.html"
        links = "".join(f'<li><a href="{name}.html">{name}（{len(chunk)} 条）</a></li>'
                        for name, chunk in chunks)
        index.write_text(
            "<!DOCTYPE html><meta charset=\"utf-8\"><title>分片导航</title>"
            f"<h1>{_e(chat.title)}</h1><ul>{links}</ul>", encoding="utf-8")
        files.append(index)
    return HtmlResult(files=files, messages=len(messages), shards=len(chunks))
