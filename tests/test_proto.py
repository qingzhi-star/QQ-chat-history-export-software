"""T2：通用 wire 读取器测试（永不抛异常，截断返回前缀）。"""
from __future__ import annotations

from qqexport.qq import proto as p
from tests.helpers import synth_proto as sp


def test_parses_varint_and_length_delimited():
    buf = sp.field_varint(40011, 2) + sp.field_bytes(45101, "你好".encode())
    parsed = p.parse(buf)
    assert parsed.truncated is False
    assert parsed.fields[40011] == [2]
    assert parsed.fields[45101] == ["你好".encode()]


def test_repeated_fields_keep_order():
    buf = sp.field_varint(10, 1) + sp.field_varint(10, 2) + sp.field_varint(10, 3)
    assert p.parse(buf).fields[10] == [1, 2, 3]


def test_length_delimited_is_not_auto_nested():
    element = sp.text_element("hello")
    parsed = p.parse(element)
    # 任何 value 都只能是 int 或 bytes，不能悄悄把文本解析成 dict
    for values in parsed.fields.values():
        for value in values:
            assert isinstance(value, (int, bytes))
    assert isinstance(parsed.fields[45101][0], bytes)


def test_as_message_parses_explicit_submessage():
    child = sp.text_element("hi")
    parsed = p.as_message(child)
    assert parsed.fields[45002] == [1]
    assert parsed.fields[45101] == [b"hi"]
    assert p.get_text(parsed, 45101) == "hi"


def test_truncated_length_returns_prefix():
    buf = sp.field_varint(1, 7) + sp.field_bytes(2, b"abcdef")[:-2]
    parsed = p.parse(buf)
    assert parsed.fields[1] == [7]
    assert 2 not in parsed.fields
    assert parsed.truncated is True


def test_invalid_varint_does_not_raise():
    parsed = p.parse(b"\x80\x80\x80")
    assert parsed.truncated is True
    assert parsed.fields == {}


def test_wire_group_types_stop_without_raising():
    parsed = p.parse(b"\x0b")          # field 1, wire 3 (start group)
    assert parsed.truncated is True


def test_empty_input_is_empty_not_error():
    parsed = p.parse(b"")
    assert parsed.fields == {} and parsed.truncated is False


def test_fixed32_and_fixed64_return_ints():
    buf = b"\x0d" + b"\x01\x00\x00\x00" + b"\x11" + b"\x02\x00\x00\x00\x00\x00\x00\x00"
    parsed = p.parse(buf)
    assert parsed.fields[1] == [1]
    assert parsed.fields[2] == [2]


def test_get_helpers_tolerate_bad_types():
    parsed = p.parse(sp.field_bytes(45101, b"abc") + sp.field_varint(45002, 1))
    assert p.get_text(parsed, 45101) == "abc"
    assert p.get_int(parsed, 45002) == 1
    assert p.get_text(parsed, 45002) is None       # 类型不符 → None，不抛
    assert p.get_int(parsed, 45101) is None
    assert p.get_bytes(parsed, 45101) == b"abc"
    assert p.get_first(parsed, 999) is None
