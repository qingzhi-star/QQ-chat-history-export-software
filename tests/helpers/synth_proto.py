"""T1：合成 QQ NT `40800` protobuf 构造器（测试专用，不含任何真实数据）。

结构依据 2026-10-29 只读探测：`40800` 是重复字段，每个元素是一个 MsgContent，
元素内用 45002 判别内容类型。字段号只用于测试；生产字段号在 qq/msgmap.py。
"""
from __future__ import annotations

# 测试用字段号（与 spec §7.1 / 探测一致；生产代码不得 import 本模块）
F_ELEMENT_ID = 45001
F_CONTENT_TYPE = 45002
F_TEXT = 45101
F_TEXT_FLAG = 45102
F_IMG_NAME = 45402
F_IMG_SIZE = 45405
F_IMG_MD5 = 45406
F_IMG_W = 45411
F_IMG_H = 45412
F_IMG_LOCAL = 45812
F_FILE_EXT = 45419
F_VOICE_MS = 45906
F_VOICE_FMT = 45907
F_VOICE_FLAG = 45550
F_FACE_ID = 47601
F_FACE_NAME = 47602
F_REPLY_BODY = 47710
F_REPLY_UID = 47703
F_REPLY_TS = 47404
F_REPLY_SUMMARY = 47413
F_FORWARD_NT_JSON = 47901
F_FORWARD_LEGACY_XML = 48602
F_FORWARD_LEGACY_RESID = 48601
F_POKE_TEXT = 47413
F_UNKNOWN = 49999

BLOB_FIELD = 40800


def varint(n: int) -> bytes:
    if n < 0:
        raise ValueError("varint must be >= 0")
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def key(field: int, wire: int) -> bytes:
    return varint((field << 3) | wire)


def field_varint(field: int, value: int) -> bytes:
    return key(field, 0) + varint(value)


def field_bytes(field: int, value: bytes) -> bytes:
    return key(field, 2) + varint(len(value)) + value


def element(content_type: int, payload: list[bytes], *,
            element_id: int = 0) -> bytes:
    out = [field_varint(F_ELEMENT_ID, element_id),
           field_varint(F_CONTENT_TYPE, content_type)]
    out.extend(payload)
    return b"".join(out)


def message(*elements: bytes) -> bytes:
    """把若干元素包成 40800 blob（每个元素一个重复字段 40800）。"""
    return b"".join(field_bytes(BLOB_FIELD, e) for e in elements)


def text_element(text: str, *, element_id: int = 0) -> bytes:
    return element(1, [field_bytes(F_TEXT, text.encode("utf-8")),
                       field_varint(F_TEXT_FLAG, 0)], element_id=element_id)


def image_element(*, md5: bytes, filename: str = "img.jpg", width: int = 100,
                  height: int = 100, size: int = 1000,
                  local_path: str | None = None, element_id: int = 0) -> bytes:
    if len(md5) != 16:
        raise ValueError("md5 must be 16 bytes")
    payload = [field_bytes(F_IMG_NAME, filename.encode()),
               field_varint(F_IMG_SIZE, size),
               field_bytes(F_IMG_MD5, md5),
               field_varint(F_IMG_W, width),
               field_varint(F_IMG_H, height)]
    if local_path is not None:
        payload.append(field_bytes(F_IMG_LOCAL, local_path.encode()))
    return element(2, payload, element_id=element_id)


def file_element(filename: str, size: int, ext: str, *,
                 element_id: int = 0) -> bytes:
    return element(3, [field_bytes(F_IMG_NAME, filename.encode()),
                       field_varint(F_IMG_SIZE, size),
                       field_bytes(F_FILE_EXT, ext.encode())],
                   element_id=element_id)


def voice_element(duration_ms: int, *, fmt: int = 1,
                  element_id: int = 0) -> bytes:
    return element(4, [field_varint(F_VOICE_MS, duration_ms),
                       field_varint(F_VOICE_FMT, fmt),
                       field_varint(F_VOICE_FLAG, 4)], element_id=element_id)


def video_element(*, md5: bytes, filename: str = "v.mp4", width: int = 320,
                  height: int = 240, size: int = 5000,
                  element_id: int = 0) -> bytes:
    return element(5, [field_bytes(F_IMG_NAME, filename.encode()),
                       field_varint(F_IMG_SIZE, size),
                       field_bytes(F_IMG_MD5, md5),
                       field_varint(F_IMG_W, width),
                       field_varint(F_IMG_H, height)], element_id=element_id)


def face_element(face_id: int, name: str = "微笑", *, element_id: int = 0) -> bytes:
    return element(6, [field_varint(F_FACE_ID, face_id),
                       field_bytes(F_FACE_NAME, name.encode())],
                   element_id=element_id)


def reply_element(quoted_message: bytes, *, sender_uid: str = "u_peer",
                  timestamp: int = 0, summary: str = "",
                  element_id: int = 0) -> bytes:
    return element(7, [field_bytes(F_REPLY_BODY, quoted_message),
                       field_bytes(F_REPLY_UID, sender_uid.encode()),
                       field_varint(F_REPLY_TS, timestamp),
                       field_bytes(F_REPLY_SUMMARY, summary.encode())],
                   element_id=element_id)


def forward_nt_element(json_text: str, *, element_id: int = 0) -> bytes:
    return element(10, [field_bytes(F_FORWARD_NT_JSON, json_text.encode())],
                   element_id=element_id)


def legacy_forward_element(xml_text: str, *, resid: str = "resid",
                           element_id: int = 0) -> bytes:
    return element(16, [field_bytes(F_FORWARD_LEGACY_RESID, resid.encode()),
                        field_bytes(F_FORWARD_LEGACY_XML, xml_text.encode())],
                   element_id=element_id)


def gray_element(text: str, *, element_id: int = 0) -> bytes:
    return element(8, [field_bytes(F_TEXT, text.encode("utf-8"))],
                   element_id=element_id)


def poke_element(text: str, *, element_id: int = 0) -> bytes:
    return element(9, [field_bytes(F_POKE_TEXT, text.encode("utf-8"))],
                   element_id=element_id)


def unknown_element(field: int, payload: bytes = b"\x01\x02", *,
                    element_id: int = 0) -> bytes:
    return element(99, [field_bytes(field, payload)], element_id=element_id)
