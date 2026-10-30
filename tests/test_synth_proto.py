"""T1：合成 protobuf 构造器的测试（不依赖 qq/proto，先用测试内独立 wire 读取器）。"""
from __future__ import annotations

from tests.helpers import synth_proto as sp


def read_varint(buf: bytes, i: int) -> tuple[int, int]:
    shift = val = 0
    while True:
        b = buf[i]
        i += 1
        val |= (b & 0x7F) << shift
        if not b & 0x80:
            return val, i
        shift += 7


def fields(buf: bytes) -> list[tuple[int, int, bytes | int]]:
    out: list[tuple[int, int, bytes | int]] = []
    i = 0
    while i < len(buf):
        key, i = read_varint(buf, i)
        f, wt = key >> 3, key & 7
        if wt == 0:
            v, i = read_varint(buf, i)
            out.append((f, wt, v))
        elif wt == 2:
            ln, i = read_varint(buf, i)
            out.append((f, wt, buf[i:i + ln]))
            i += ln
        elif wt == 1:
            out.append((f, wt, buf[i:i + 8]))
            i += 8
        elif wt == 5:
            out.append((f, wt, buf[i:i + 4]))
            i += 4
        else:
            raise AssertionError(f"unexpected wire type {wt}")
    return out


def test_varint_roundtrip():
    for n in (0, 1, 127, 128, 300, 2**32 - 1):
        encoded = sp.varint(n)
        value, end = read_varint(encoded, 0)
        assert value == n
        assert end == len(encoded)


def test_message_wraps_each_element_in_field_40800():
    blob = sp.message(sp.text_element("你好"), sp.text_element("world"))
    top = fields(blob)
    assert [f for f, _, _ in top] == [40800, 40800]
    assert all(wt == 2 for _, wt, _ in top)
    child = fields(top[0][2])
    assert child[0][0] == 45001
    assert (45002, 0) == (child[1][0], child[1][1])
    text = [v for f, _, v in child if f == 45101]
    assert text == [b"\xe4\xbd\xa0\xe5\xa5\xbd"]


def test_text_element_has_content_type_1():
    child = fields(sp.text_element("abc"))
    assert (45002, 0, 1) in child


def test_image_element_carries_raw_md5_and_dims():
    md5 = bytes(range(16))
    child = fields(sp.image_element(md5=md5, filename="a.jpg", width=640,
                                    height=480, size=1234))
    by_field = {f: v for f, _, v in child}
    assert by_field[45002] == 2
    assert by_field[45406] == md5
    assert by_field[45411] == 640
    assert by_field[45412] == 480
    assert by_field[45405] == 1234
    assert by_field[45402] == b"a.jpg"


def test_file_and_voice_elements():
    f = {x: v for x, _, v in fields(sp.file_element("m.pdf", 2048, "pdf"))}
    assert f[45002] == 3
    assert f[45405] == 2048
    assert f[45419] == b"pdf"
    v = {x: v for x, _, v in fields(sp.voice_element(12000))}
    assert v[45002] == 4
    assert v[45906] == 12000
    assert v[45907] == 1


def test_reply_element_embeds_nested_message():
    nested = sp.message(sp.text_element("原消息"))
    child = fields(sp.reply_element(nested, sender_uid="u_x", summary="原消息"))
    by = {f: v for f, _, v in child}
    assert by[45002] == 7
    assert by[47710] == nested


def test_forward_and_unknown_elements():
    nt = {f: v for f, _, v in fields(sp.forward_nt_element('{"app":"x"}'))}
    assert nt[45002] == 10
    assert nt[47901] == b'{"app":"x"}'
    legacy = {f: v for f, _, v in fields(
        sp.legacy_forward_element("<msg><dataitem/></msg>"))}
    assert legacy[45002] == 16
    assert b"<dataitem/>" in legacy[48602]
    unknown = fields(sp.unknown_element(49999, b"\x01\x02"))
    assert (49999, 2, b"\x01\x02") in unknown


def test_gray_and_poke_elements():
    gray = {f: v for f, _, v in fields(sp.gray_element("张三 加入了群聊"))}
    assert gray[45002] == 8
    poke = {f: v for f, _, v in fields(sp.poke_element("你戳了戳我"))}
    assert poke[45002] == 9
    assert poke[47413] == "你戳了戳我".encode()
