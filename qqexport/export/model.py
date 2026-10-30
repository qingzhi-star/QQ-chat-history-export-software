"""P1 共享中间模型（spec §8.1）；txt/html/pdf 都投影自这里。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class MediaRef:
    kind: str                       # image | video | voice | file
    source: Path | None = None
    missing: bool = True
    width: int | None = None
    height: int | None = None
    size: int | None = None
    duration_ms: int | None = None
    filename: str | None = None
    rel_path: str | None = None


@dataclass(frozen=True)
class Segment:
    kind: str
    text: str | None = None
    media: MediaRef | None = None
    quoted: "Message | None" = None
    forward_previews: list[tuple[str, str]] | None = None
    forward_meta: dict | None = None
    unknown_field: int | None = None
    raw_hex: str | None = None


@dataclass(frozen=True)
class Message:
    msg_id: int
    ts: int
    sender_uid: str | None
    sender_uin: str | None
    sender_name: str
    is_self: bool
    segments: list[Segment] = field(default_factory=list)
    outer_type: int = 0
    sub_type: int = 0


@dataclass(frozen=True)
class Chat:
    kind: str                       # c2c | group
    peer_id: str
    title: str
    subtitle: str | None = None
    messages: list[Message] = field(default_factory=list)
