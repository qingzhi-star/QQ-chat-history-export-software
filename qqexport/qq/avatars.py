"""头像与表情资源：导出时复制到 media/，避免 HTML 依赖网络。"""
from __future__ import annotations

import base64
import hashlib
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from qqexport.export.model import Message, Segment

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) qqexport/0.1"
_cache: dict[str, bytes | None] = {}


def clear_cache() -> None:
    _cache.clear()


def fetch_avatar(uin: str | None, timeout: float = 4.0) -> bytes | None:
    if not uin or not str(uin).isdigit():
        return None
    key = str(uin)
    if key in _cache:
        return _cache[key]
    url = f"https://q1.qlogo.cn/g?b=qq&nk={key}&s=100"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read(512 * 1024)
        if not (data.startswith(b"\xff\xd8") or data.startswith(b"\x89PNG")):
            data = None
    except Exception:
        data = None
    _cache[key] = data
    return data


def fetch_group_avatar(group_id: str | None, timeout: float = 4.0) -> bytes | None:
    if not group_id or not str(group_id).isdigit():
        return None
    key = "g" + str(group_id)
    if key in _cache:
        return _cache[key]
    url = f"https://p.qlogo.cn/gh/{group_id}/{group_id}/100"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read(512 * 1024)
        if not (data.startswith(b"\xff\xd8") or data.startswith(b"\x89PNG")):
            data = None
    except Exception:
        data = None
    _cache[key] = data
    return data


def add_avatars(messages: list[Message]) -> list[Message]:
    """给有 QQ 号的发送者下载头像，并以 data URI 内嵌（不落 media 文件）。"""
    uins: set[str] = {m.sender_uin for m in messages
                      if m.sender_uin and m.sender_uin.isdigit()}
    embedded: dict[str, str] = {}
    if uins:
        with ThreadPoolExecutor(max_workers=10) as pool:
            fetched = dict(zip(sorted(uins), pool.map(fetch_avatar,
                                                       sorted(uins))))
    else:
        fetched = {}
    for uin, data in fetched.items():
        if data is not None:
            mime = "image/png" if data.startswith(b"\x89PNG") else "image/jpeg"
            embedded[uin] = (f"data:{mime};base64,"
                             + base64.b64encode(data).decode("ascii"))
    out = []
    for m in messages:
        data = embedded.get(m.sender_uin or "")
        out.append(m if data is None else
                   __import__("dataclasses").replace(m, avatar_data=data))
    return out


def emoji_path(nt_data: Path, face_id: int | None) -> Path | None:
    if face_id is None:
        return None
    root = nt_data / "Emoji" / "BaseEmojiSyastems" / "EmojiSystermResource"
    p = root / str(face_id) / "png" / f"{face_id}.png"
    return p if p.is_file() else None


def add_emoji(messages: list[Message], nt_data: Path) -> dict[int, str]:
    """把用到的标准表情变成 data URI，返回 face_id → data URI。"""
    ids = {s.face_id for m in messages for s in m.segments
           if s.kind == "face" and s.face_id is not None}
    mapping: dict[int, str] = {}
    for face_id in ids:
        src = emoji_path(nt_data, face_id)
        if src is None:
            continue
        mapping[face_id] = ("data:image/png;base64,"
                            + base64.b64encode(src.read_bytes()).decode("ascii"))
    return mapping


def nt_data_dir(msg_db: Path) -> Path:
    """nt_msg.db 位于 nt_qq/nt_db，头像/表情在 nt_qq/nt_data。"""
    return Path(msg_db).parent.parent / "nt_data"


def avatar_fingerprint(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()
