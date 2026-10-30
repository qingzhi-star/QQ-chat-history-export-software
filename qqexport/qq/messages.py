"""T4：行 → Message 的基础解析（P1）。

设计：单条消息失败只降级为 unknown segment，绝不中止整批。
媒体解析通过 `ParseContext.resolve_media` 注入（T7 实现）；T4 只填元数据。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable
import sqlite3

from qqexport.export.model import MediaRef, Message, Segment
from . import msgmap as mm
from . import proto
from . import tables


def _default_name(uid: str | None, uin: str | None,
                  group: str | None) -> str:
    if uin:
        return f"QQ号{uin}"
    if uid:
        return f"未知用户{uid[:8]}"
    return "未知用户"


@dataclass
class ParseContext:
    account_uin: str = ""
    kind: str = "c2c"
    group_id: str | None = None
    display_name: Callable[[str | None, str | None, str | None], str] = _default_name
    resolve_media: Callable[[str, str | None, str | None, str | None],
                            MediaRef] | None = None


def _as_int(value: object) -> int:
    try:
        return int(value)        # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _as_str(value: object) -> str | None:
    return str(value) if isinstance(value, str) else None


def _media(child: proto.Parsed, kind: str, ctx: ParseContext) -> MediaRef:
    md5 = proto.get_bytes(child, mm.F_IMAGE_MD5)
    md5_hex = md5.hex() if md5 is not None and len(md5) == 16 else None
    filename = proto.get_text(child, mm.F_IMAGE_NAME)
    local_path = proto.get_text(child, mm.F_IMAGE_LOCAL)
    size = proto.get_int(child, mm.F_IMAGE_SIZE)
    width = proto.get_int(child, mm.F_IMAGE_W)
    height = proto.get_int(child, mm.F_IMAGE_H)
    duration = proto.get_int(child, mm.F_VOICE_MS)
    if ctx.resolve_media is not None:
        return ctx.resolve_media(kind, md5_hex, local_path, filename)
    return MediaRef(kind=kind, missing=True, width=width, height=height,
                    size=size, duration_ms=duration, filename=filename)


def parse_element(child_bytes: bytes, ctx: ParseContext) -> Segment:
    child = proto.as_message(child_bytes)
    content_type = proto.get_int(child, mm.F_CONTENT_TYPE) or 0
    if content_type == mm.CONTENT_TEXT:
        return Segment(kind="text", text=proto.get_text(child, mm.F_TEXT) or "")
    if content_type == mm.CONTENT_IMAGE:
        return Segment(kind="image", media=_media(child, "image", ctx))
    if content_type == mm.CONTENT_FILE:
        return Segment(kind="file", media=_media(child, "file", ctx))
    if content_type == mm.CONTENT_VOICE:
        return Segment(kind="voice", media=_media(child, "voice", ctx))
    if content_type == mm.CONTENT_VIDEO:
        return Segment(kind="video", media=_media(child, "video", ctx))
    if content_type == mm.CONTENT_FACE:
        name = proto.get_text(child, mm.F_FACE_NAME)
        return Segment(kind="face", text=name or "[表情]")
    if content_type == mm.CONTENT_MARKET_FACE:
        return Segment(kind="market_face", text="[商城表情]")
    if content_type == mm.CONTENT_GRAY_TIP:
        return Segment(kind="gray_tip", text=proto.get_text(child, mm.F_TEXT) or "")
    if content_type == mm.CONTENT_RED_BAG:
        return Segment(kind="red_bag", text="[红包]")
    if content_type == mm.CONTENT_MARKDOWN:
        return Segment(kind="markdown", text=proto.get_text(child, mm.F_TEXT) or "")
    if content_type == mm.CONTENT_ARK:
        return Segment(kind="ark", text="[卡片]")
    if content_type == mm.CONTENT_REPLY:
        return Segment(kind="reply", text=proto.get_text(child, mm.F_REPLY_SUMMARY) or "")
    if content_type == mm.CONTENT_LEGACY_FORWARD:
        return Segment(kind="forward_legacy", text="[转发]")
    if content_type == mm.CONTENT_LIVE:
        return Segment(kind="live", text="[直播]")
    return Segment(kind="unknown", unknown_field=content_type,
                   raw_hex=child_bytes[:64].hex())


def parse_message(row: dict[str, object], ctx: ParseContext) -> Message:
    blob = row.get(mm.COL_BLOB)
    segments: list[Segment] = []
    if isinstance(blob, (bytes, bytearray)):
        raw = bytes(blob)
        parsed = proto.parse(raw)
        children = [bytes(v) for v in parsed.fields.get(mm.BLOB_FIELD, [])
                    if isinstance(v, (bytes, bytearray))]
        for child in children:
            try:
                segments.append(parse_element(child, ctx))
            except Exception:            # 单元素失败只降级
                segments.append(Segment(kind="unknown",
                                        raw_hex=child[:64].hex()))
        if not children and raw:
            segments.append(Segment(kind="unknown", raw_hex=raw[:64].hex()))

    uid = _as_str(row.get(mm.COL_SENDER_UID))
    uin = _as_str(row.get(mm.COL_SENDER_UIN))
    group = ctx.group_id
    if group is None and ctx.kind == tables.KIND_GROUP:
        group = _as_str(row.get(mm.COL_PEER_ID))
    name = ctx.display_name(uid, uin, group)
    direction = _as_int(row.get(mm.COL_DIRECTION))
    is_self = direction == 1 or (uin is not None and uin == ctx.account_uin)
    return Message(
        msg_id=_as_int(row.get(mm.COL_MSG_ID)),
        ts=_as_int(row.get(mm.COL_TIMESTAMP)),
        sender_uid=uid,
        sender_uin=uin,
        sender_name=name,
        is_self=is_self,
        segments=segments,
        outer_type=_as_int(row.get(mm.COL_OUTER_TYPE)),
        sub_type=_as_int(row.get(mm.COL_SUB_TYPE)),
    )


def iter_messages(con: sqlite3.Connection, kind: str, peer: str,
                  ctx: ParseContext):
    for row in tables.iter_chat_rows(con, kind, peer):
        yield parse_message(row, ctx)
