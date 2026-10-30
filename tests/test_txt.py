"""T8：txt 导出测试。"""
from __future__ import annotations

import codecs
from datetime import datetime

from qqexport.export import txt
from qqexport.export.model import Chat, MediaRef, Message, Segment

NOW = datetime(2026, 10, 30, 12, 0, 0)


def msg(ts, name, *segments, is_self=False):
    return Message(msg_id=ts, ts=ts, sender_uid="u", sender_uin=None,
                   sender_name=name, is_self=is_self, segments=list(segments),
                   outer_type=2, sub_type=1)


def chat():
    t1 = int(datetime(2026, 10, 29, 14, 32, 7).timestamp())
    t2 = int(datetime(2026, 10, 30, 9, 0, 0).timestamp())
    image = Segment(kind="image", media=MediaRef(
        kind="image", missing=False, rel_path="media/img_aabbccdd.jpg"))
    reply = Segment(kind="reply", text="嗯",
                    quoted=msg(t1, "张三", Segment(kind="text", text="今天开会吗")))
    legacy = Segment(kind="forward_legacy",
                     forward_previews=[("李四", "你好"), ("王五", "在吗")])
    return Chat(kind="group", peer_id="123", title="某某群", subtitle="群号 123",
                messages=[msg(t1, "张三", Segment(kind="text", text="今天开会吗")),
                          msg(t2, "李四", Segment(kind="text", text="开的"),
                              image, reply, legacy)])


def test_txt_header_dates_and_segments(tmp_path):
    out = tmp_path / "a.txt"
    res = txt.write_txt(chat(), out, txt.TxtOptions(now=NOW, account_uin="123456789"))
    data = out.read_bytes()
    assert data.startswith(codecs.BOM_UTF8)
    text = data.decode("utf-8-sig")
    assert "群聊：某某群  (群号 123)" in text
    assert "消息条数：2" in text
    assert "2026-10-29 星期四" in text
    assert "2026-10-30 星期五" in text
    assert "[14:32:07] 张三：今天开会吗" in text
    assert "[图片] media/img_aabbccdd.jpg" in text
    assert "[回复 张三：今天开会吗] 嗯" in text
    assert "    李四：你好" in text
    assert res.messages == 2


def test_txt_gbk_replacement_count(tmp_path):
    out = tmp_path / "b.txt"
    c = Chat(kind="c2c", peer_id="u", title="T",
             messages=[msg(1700000000, "A", Segment(kind="text", text="🙂emoji"))])
    res = txt.write_txt(c, out, txt.TxtOptions(encoding="gbk", now=NOW))
    assert res.replacements >= 1
    assert b"?" in out.read_bytes()


def test_txt_empty_chat(tmp_path):
    out = tmp_path / "c.txt"
    c = Chat(kind="c2c", peer_id="u", title="T")
    res = txt.write_txt(c, out, txt.TxtOptions(now=NOW))
    assert res.messages == 0
    assert "消息条数：0" in out.read_text(encoding="utf-8-sig")
