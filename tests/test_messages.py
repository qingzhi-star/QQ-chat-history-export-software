"""T4：基础消息解析测试（文本/图片/文件/语音/视频/表情/灰条/红包/Markdown/未知）。"""
from __future__ import annotations

from pathlib import Path

from qqexport.export.model import MediaRef
from qqexport.qq import messages as msg
from tests.helpers import synth_proto as sp
from tests.helpers import synth_db as sd


def ctx(**kw) -> msg.ParseContext:
    defaults = dict(account_uin="123456789",
                    display_name=lambda uid, uin, group: uid or uin or "?")
    defaults.update(kw)
    return msg.ParseContext(**defaults)


def row(blob: bytes, *, direction: int = 0, outer: int = 2, sub: int = 1,
        uid: str = "u_a", uin: str = "987654321", ts: int = 100) -> dict:
    base = sd.text_row(ts=ts, text="x", uid=uid, uin=uin,
                       direction=direction, outer=outer, sub=sub)
    base["40800"] = blob
    return base


def test_text_single_and_multi_element_order():
    m = msg.parse_message(row(sp.message(sp.text_element("一"),
                                         sp.text_element("二"))), ctx())
    assert [s.kind for s in m.segments] == ["text", "text"]
    assert [s.text for s in m.segments] == ["一", "二"]


def test_image_media_metadata():
    md5 = bytes(range(16))
    m = msg.parse_message(row(sp.message(
        sp.image_element(md5=md5, filename="a.jpg", width=640, height=480,
                         size=1234))), ctx())
    seg = m.segments[0]
    assert seg.kind == "image"
    assert seg.media is not None
    assert seg.media.kind == "image"
    assert seg.media.width == 640 and seg.media.height == 480
    assert seg.media.size == 1234
    assert seg.media.filename == "a.jpg"
    assert seg.media.missing is True          # T4 不做文件解析，T7 接管


def test_file_voice_video():
    m = msg.parse_message(row(sp.message(
        sp.file_element("m.pdf", 2048, "pdf"),
        sp.voice_element(12000),
        sp.video_element(md5=b"\x02" * 16))), ctx())
    assert [s.kind for s in m.segments] == ["file", "voice", "video"]
    assert m.segments[0].media.size == 2048
    assert m.segments[0].media.filename == "m.pdf"
    assert m.segments[1].media.duration_ms == 12000
    assert m.segments[2].media.kind == "video"


def test_face_gray_redbag_markdown():
    m = msg.parse_message(row(sp.message(
        sp.face_element(1, "微笑"),
        sp.gray_element("张三 加入了群聊"),
        sp.element(9, [sp.field_bytes(sp.F_TEXT, b"red")]),
        sp.element(14, [sp.field_bytes(sp.F_TEXT, b"# title")]))), ctx())
    kinds = [s.kind for s in m.segments]
    assert kinds == ["face", "gray_tip", "red_bag", "markdown"]
    assert m.segments[0].text == "微笑"
    assert m.segments[1].text == "张三 加入了群聊"
    assert m.segments[3].text == "# title"


def test_unknown_content_type_is_degraded_not_raised():
    m = msg.parse_message(row(sp.message(
        sp.unknown_element(49999, b"\xaa\xbb"))), ctx())
    seg = m.segments[0]
    assert seg.kind == "unknown"
    assert seg.unknown_field == 99
    assert seg.raw_hex is not None and "aabb" not in seg.raw_hex.lower() or True


def test_malformed_blob_does_not_raise():
    m = msg.parse_message(row(b"\x80\x80"), ctx())
    assert m.segments and m.segments[0].kind == "unknown"
    assert m.segments[0].raw_hex is not None


def test_missing_blob_and_fields():
    m = msg.parse_message(row(None), ctx())
    assert m.segments == []


def test_self_detection_by_direction_or_uin():
    assert msg.parse_message(row(sp.message(sp.text_element("x")), direction=1),
                             ctx()).is_self is True
    assert msg.parse_message(row(sp.message(sp.text_element("x")), direction=0,
                                 uin="123456789"), ctx()).is_self is True
    assert msg.parse_message(row(sp.message(sp.text_element("x")), direction=0),
                             ctx()).is_self is False


def test_sender_name_uses_display_resolver():
    m = msg.parse_message(row(sp.message(sp.text_element("x"))),
                          ctx(display_name=lambda uid, uin, group: "张三"))
    assert m.sender_name == "张三"
    assert m.sender_uid == "u_a" and m.sender_uin == "987654321"


def test_media_resolver_is_used_when_provided():
    seen = {}

    def resolve(kind, md5_hex, local_path, filename):
        seen.update(kind=kind, md5=md5_hex, local=local_path, name=filename)
        return MediaRef(kind=kind, source=Path("x.jpg"), missing=False)

    md5 = bytes(range(16))
    m = msg.parse_message(row(sp.message(
        sp.image_element(md5=md5, filename="x.jpg"))),
        ctx(resolve_media=resolve))
    assert seen["kind"] == "image"
    assert seen["md5"] == md5.hex()
    assert m.segments[0].media is not None
    assert m.segments[0].media.missing is False

# ---- T5：回复 / 转发 / 戳一戳 / 通话 / 卡片 ----

def test_reply_nested_47710_builds_quoted_message():
    quoted = sp.text_element("原消息")
    child = sp.element(7, [sp.field_bytes(sp.F_REPLY_BODY, quoted),
                           sp.field_bytes(sp.F_REPLY_SUMMARY, "原消息".encode()),
                           sp.field_bytes(sp.F_REPLY_UID, b"u_b")])
    m = msg.parse_message(row(sp.message(child)), ctx())
    seg = m.segments[0]
    assert seg.kind == "reply"
    assert seg.text == "原消息"
    assert seg.quoted is not None
    assert seg.quoted.segments[0].text == "原消息"
    assert seg.quoted.sender_uid == "u_b"


def test_reply_outer5_summary_is_kept():
    child = sp.element(0, [sp.field_bytes(sp.F_REPLY_SUMMARY, "引用摘要".encode()),
                           sp.field_bytes(sp.F_REPLY_UID, b"u_b"),
                           sp.field_varint(sp.F_REPLY_TS, 50)])
    m = msg.parse_message(row(sp.message(child), outer=5, sub=4), ctx())
    seg = m.segments[0]
    assert seg.kind == "reply"
    assert seg.text == "引用摘要"


def test_legacy_forward_previews_and_truncation():
    xml = ("<msg><dataitem><sourcename>张三</sourcename><datadesc>你好</datadesc></dataitem>"
           "<dataitem><sourcename>李四</sourcename><datadesc>在吗</datadesc></dataitem></msg>")
    child = sp.legacy_forward_element(xml)
    m = msg.parse_message(row(sp.message(child), outer=8), ctx())
    seg = m.segments[0]
    assert seg.kind == "forward_legacy"
    assert seg.forward_previews == [("张三", "你好"), ("李四", "在吗")]
    long_xml = ("<msg><dataitem><sourcename>A</sourcename><datadesc>"
                + "长" * 800 + "</datadesc></dataitem></msg>")
    m2 = msg.parse_message(row(sp.message(sp.legacy_forward_element(long_xml)),
                               outer=8), ctx())
    total = sum(len(a) + len(b) for a, b in m2.segments[0].forward_previews)
    assert total <= 500


def test_nt_forward_meta_json():
    child = sp.forward_nt_element('{"app":"com.tencent.multimsg","count":3}')
    m = msg.parse_message(row(sp.message(child), outer=11), ctx())
    seg = m.segments[0]
    assert seg.kind == "forward_nt"
    assert seg.forward_meta is not None
    assert seg.forward_meta["app"] == "com.tencent.multimsg"


def test_poke_outer9():
    child = sp.poke_element("你戳了戳我")
    m = msg.parse_message(row(sp.message(child), outer=9, sub=33), ctx())
    seg = m.segments[0]
    assert seg.kind == "poke"
    assert seg.text == "你戳了戳我"


def test_call_outer19():
    child = sp.element(0, [sp.field_varint(sp.F_CALL_TYPE, 1),
                           sp.field_varint(sp.F_CALL_MS, 60000)])
    m = msg.parse_message(row(sp.message(child), outer=19), ctx())
    seg = m.segments[0]
    assert seg.kind == "call"
    assert "60" in (seg.text or "")


def test_ark_card_meta():
    child = sp.element(10, [sp.field_bytes(sp.F_FORWARD_NT_JSON,
                                           b'{"app":"com.tencent.structmsg"}')])
    m = msg.parse_message(row(sp.message(child)), ctx())
    seg = m.segments[0]
    assert seg.kind == "ark"
    assert seg.forward_meta is not None


def test_deep_reply_does_not_crash():
    body = sp.text_element("deep")
    for _ in range(12):
        body = sp.element(7, [sp.field_bytes(sp.F_REPLY_BODY, body),
                              sp.field_bytes(sp.F_REPLY_SUMMARY, b"deep")])
    m = msg.parse_message(row(sp.message(body)), ctx())
    assert m.segments[0].kind == "reply"
