"""T2：字段号唯一真源测试。"""
from __future__ import annotations

from qqexport.qq import msgmap as m


def test_message_columns_are_the_39_real_columns():
    assert len(m.MSG_COLUMNS) == 39
    assert len(set(m.MSG_COLUMNS)) == 39
    for required in ("40001", "40011", "40012", "40013", "40020", "40021",
                     "40030", "40033", "40050", "40800"):
        assert required in m.MSG_COLUMNS


def test_outer_types_cover_spec():
    assert m.OUTER_STANDARD == 2
    assert m.OUTER_FILE == 3
    assert m.OUTER_REPLY_OR_CARD == 5
    assert m.OUTER_LEGACY_FORWARD == 8
    assert m.OUTER_POKE == 9
    assert m.OUTER_FORWARD_NT == 11
    assert m.OUTER_STATUS_OR_MARKET_FACE == 17
    assert m.OUTER_CALL == 19


def test_content_kinds_cover_spec():
    assert m.CONTENT_TEXT == 1
    assert m.CONTENT_IMAGE == 2
    assert m.CONTENT_FILE == 3
    assert m.CONTENT_VOICE == 4
    assert m.CONTENT_VIDEO == 5
    assert m.CONTENT_FACE == 6
    assert m.CONTENT_REPLY == 7
    assert m.CONTENT_GRAY_TIP == 8
    assert m.CONTENT_RED_BAG == 9
    assert m.CONTENT_ARK == 10
    assert m.CONTENT_MARKET_FACE == 11
    assert m.CONTENT_MARKDOWN == 14
    assert m.CONTENT_LEGACY_FORWARD == 16
    assert m.CONTENT_LIVE == 21


def test_content_kind_names_are_stable():
    assert m.CONTENT_KIND[m.CONTENT_TEXT] == "text"
    assert m.CONTENT_KIND[m.CONTENT_IMAGE] == "image"
    assert m.CONTENT_KIND[m.CONTENT_REPLY] == "reply"
    assert m.CONTENT_KIND[m.CONTENT_LEGACY_FORWARD] == "forward_legacy"
    assert m.CONTENT_KIND[m.CONTENT_ARK] == "ark"
    assert m.CONTENT_KIND[m.CONTENT_MARKDOWN] == "markdown"


def test_element_fields_are_distinct_constants():
    fields = [m.F_TEXT, m.F_IMAGE_MD5, m.F_VOICE_MS, m.F_REPLY_BODY,
              m.F_FORWARD_NT_JSON, m.F_FORWARD_LEGACY_XML, m.F_POKE_TEXT]
    assert len(set(fields)) == len(fields)
    assert m.F_CONTENT_TYPE == 45002
    assert m.BLOB_FIELD == 40800
    # 图片名与文件名共用 45402（真实探测），不是笔误
    assert m.F_IMAGE_NAME == m.F_FILE_NAME == 45402
