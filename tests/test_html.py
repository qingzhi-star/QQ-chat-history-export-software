"""T9：HTML 导出测试（golden/转义/零外链/分片）。"""
from __future__ import annotations

import re
from datetime import datetime

from qqexport.export import html as h
from qqexport.export.model import Chat, MediaRef, Message, Segment

NOW = datetime(2026, 10, 30, 12, 0, 0)


def m(ts, name, seg, self_=False):
    return Message(msg_id=ts, ts=ts, sender_uid="u", sender_uin=None,
                   sender_name=name, is_self=self_, segments=[seg],
                   outer_type=2, sub_type=1)


def test_html_escapes_and_is_offline(tmp_path):
    chat = Chat(kind="c2c", peer_id="u", title="标题<script>", messages=[
        m(1700000000, "张三", Segment(kind="text", text="<script>alert(1)</script>")),
        m(1700000100, "我", Segment(kind="image", media=MediaRef(
            kind="image", missing=False, rel_path="media/img_abcd1234.jpg")), self_=True),
    ])
    res = h.write_html(chat, tmp_path, h.HtmlOptions(now=NOW, account_uin="123456789"))
    text = res.files[0].read_text(encoding="utf-8")
    assert "<script>alert(1)</script>" not in text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in text
    assert "标题&lt;script&gt;" in text
    assert "msg self" in text and "msg other" in text
    assert "media/img_abcd1234.jpg" in text
    assert not re.search(r"https?://|//cdn|url\(", text)
    assert res.messages == 2 and res.shards == 1


def test_html_shards_by_year_and_nav(tmp_path):
    msgs = [m(int(datetime(2025, 1, 1).timestamp()) + i, "A",
              Segment(kind="text", text=f"m{i}")) for i in range(3)]
    msgs += [m(int(datetime(2026, 1, 1).timestamp()) + i, "A",
               Segment(kind="text", text=f"n{i}")) for i in range(3)]
    chat = Chat(kind="group", peer_id="1", title="G", messages=msgs)
    res = h.write_html(chat, tmp_path, h.HtmlOptions(now=NOW, shard_size=2))
    names = sorted(p.name for p in res.files)
    assert "chat-2025-1.html" in names and "chat-2026-1.html" in names
    assert "index.html" in names
    assert res.shards >= 4


def test_html_reply_and_unknown(tmp_path):
    quoted = m(1700000000, "张三", Segment(kind="text", text="原话"))
    chat = Chat(kind="c2c", peer_id="u", title="T", messages=[
        Message(msg_id=1, ts=1700000001, sender_uid="u", sender_uin=None,
                sender_name="李四", is_self=False,
                segments=[Segment(kind="reply", text="回复", quoted=quoted)],
                outer_type=2, sub_type=1),
        m(1700000002, "李四", Segment(kind="unknown", unknown_field=99,
                                      raw_hex="aabb")),
    ])
    h.write_html(chat, tmp_path, h.HtmlOptions(now=NOW))
    text = (tmp_path / "chat.html").read_text(encoding="utf-8")
    assert "<blockquote>张三：原话</blockquote>" in text
    assert "未解析 字段 99" in text
