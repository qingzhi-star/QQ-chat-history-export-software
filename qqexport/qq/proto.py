"""通用 protobuf wire 读取器（P1）。

设计要点：
- **永不抛异常**：截断/非法 varint/wire 3-4 都返回已解析前缀并置 truncated。
- **不自动递归**：length-delimited 一律保留 `bytes`；需要当子消息时显式调用
  `as_message()`。否则中文文本会被误判成字段（见 P1-D2）。
- 未知字段原样保留，供 unknown_fields 统计。
"""
from __future__ import annotations

from dataclasses import dataclass, field

Value = int | bytes


@dataclass(frozen=True)
class Parsed:
    fields: dict[int, list[Value]] = field(default_factory=dict)
    truncated: bool = False


def _read_varint(buf: bytes, i: int) -> tuple[int, int]:
    shift = 0
    value = 0
    while True:
        if i >= len(buf):
            raise ValueError("truncated varint")
        b = buf[i]
        i += 1
        value |= (b & 0x7F) << shift
        if not b & 0x80:
            return value, i
        shift += 7
        if shift > 70:
            raise ValueError("varint too long")


def parse(buf: bytes) -> Parsed:
    fields: dict[int, list[Value]] = {}
    i = 0
    while i < len(buf):
        try:
            key, i = _read_varint(buf, i)
        except ValueError:
            return Parsed(fields, True)
        field_no, wire = key >> 3, key & 7
        if field_no == 0:
            return Parsed(fields, True)
        try:
            if wire == 0:
                value, i = _read_varint(buf, i)
                fields.setdefault(field_no, []).append(value)
            elif wire == 1:
                if i + 8 > len(buf):
                    return Parsed(fields, True)
                fields.setdefault(field_no, []).append(
                    int.from_bytes(buf[i:i + 8], "little"))
                i += 8
            elif wire == 2:
                length, i = _read_varint(buf, i)
                if i + length > len(buf):
                    return Parsed(fields, True)
                fields.setdefault(field_no, []).append(buf[i:i + length])
                i += length
            elif wire == 5:
                if i + 4 > len(buf):
                    return Parsed(fields, True)
                fields.setdefault(field_no, []).append(
                    int.from_bytes(buf[i:i + 4], "little"))
                i += 4
            else:                      # wire 3/4：已废弃 group，停止该层
                return Parsed(fields, True)
        except ValueError:
            return Parsed(fields, True)
    return Parsed(fields, False)


def as_message(buf: bytes) -> Parsed:
    if not isinstance(buf, (bytes, bytearray)):
        return Parsed({}, True)
    return parse(bytes(buf))


def get_first(parsed: Parsed, field_no: int) -> Value | None:
    values = parsed.fields.get(field_no)
    return values[0] if values else None


def get_int(parsed: Parsed, field_no: int) -> int | None:
    value = get_first(parsed, field_no)
    return value if isinstance(value, int) else None


def get_bytes(parsed: Parsed, field_no: int) -> bytes | None:
    value = get_first(parsed, field_no)
    return value if isinstance(value, (bytes, bytearray)) else None


def get_text(parsed: Parsed, field_no: int) -> str | None:
    value = get_bytes(parsed, field_no)
    if value is None:
        return None
    return bytes(value).decode("utf-8", "replace")
