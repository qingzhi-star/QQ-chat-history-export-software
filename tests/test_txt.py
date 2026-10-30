"""T8：txt 导出测试（新格式：逐行 [时间 用户名称]：内容）。"""
from __future__ import annotations

import codecs
from datetime import datetime

from qqexport.export import txt
from qqexport.export.model import Chat, MediaRef, Message, Segment

T1 = int(datetime(2026, 10, 29, 14, 32, 7).timestamp())


def msg(ts, name, *segments, is_self=False):
    return Message(msg_id=ts, ts=ts, sender_uid="u", sender_uin=None,
                   sender_name=name, is_self=is_self, segments=list(segments),
                   outer_type=2, sub_type=1)


def test_txt_plain_lines_with_image_and_file(tmp_path):
    chat = Chat(kind="group", peer_id="123", title="群", messages=[
        msg(T1, "张三", Segment(kind="text", text="你好")),
        msg(T1 + 1, "李四", Segment(kind="image"),
            Segment(kind="file", media=MediaRef(kind="file", missing=True,
                                                filename="材料.pdf"))),
    ])
    out = tmp_path / "chat.txt"
    res = txt.write_txt(chat, out, txt.TxtOptions())
    data = out.read_bytes()
    assert data.startswith(codecs.BOM_UTF8)
    text = data.decode("utf-8-sig")
    assert "[2026-10-29 14:32:07 张三]：你好" in text
    assert "[2026-10-29 14:32:08 李四]：[图片] [文件]" in text
    assert "导出时间" not in text and "-----" not in text
    assert res.messages == 2


def test_txt_reply_and_unknown(tmp_path):
    chat = Chat(kind="c2c", peer_id="u", title="T", messages=[
        msg(T1, "李四", Segment(kind="reply", text="嗯",
                                quoted=msg(T1 - 1, "张三",
                                           Segment(kind="text", text="开会吗"))),
            Segment(kind="unknown", unknown_field=17)),
    ])
    out = tmp_path / "chat.txt"
    txt.write_txt(chat, out, txt.TxtOptions())
    text = out.read_text(encoding="utf-8-sig")
    assert "[回复 张三：开会吗] 嗯" in text
    assert "[未解析]" in text


def test_txt_gbk_replacement(tmp_path):
    chat = Chat(kind="c2c", peer_id="u", title="T", messages=[
        msg(T1, "A", Segment(kind="text", text="🙂"))])
    out = tmp_path / "chat.txt"
    res = txt.write_txt(chat, out, txt.TxtOptions(encoding="gbk"))
    assert res.replacements >= 1
