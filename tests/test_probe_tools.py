"""T1：探测工具的脱敏与结构测试。"""
from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

from tests.helpers import synth_proto as sp

ROOT = Path(__file__).resolve().parent.parent


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pf = load("probe_formats")
pc = load("probe_contacts")


def test_summarize_blob_reports_fields_without_content():
    blob = sp.message(sp.text_element("秘密内容"), sp.image_element(md5=b"\x01" * 16))
    out = pf.summarize_blob(blob)
    assert out["top_fields"] == [40800]
    assert len(out["elements"]) == 2
    assert 45002 in out["elements"][0]["child_fields"]
    assert 45101 in out["elements"][0]["child_fields"]
    text = repr(out)
    assert "秘密内容" not in text
    assert "\\x01" not in text
    assert out["truncated"] is False


def test_summarize_blob_survives_truncation():
    out = pf.summarize_blob(b"\x88\x80\x80")
    assert out["truncated"] is True


def test_value_shape_classifies_without_leaking():
    assert pc.value_shape(None) == "null"
    assert pc.value_shape(123) == "number"
    assert pc.value_shape("123456789") == "digits"
    assert pc.value_shape("u_abc") == "uid"
    assert pc.value_shape("张三") == "text"
    assert pc.value_shape(b"\x00\x01") == "binary"
    assert pc.value_shape("") == "empty"


def test_column_stats_has_only_shapes(tmp_path):
    db = tmp_path / "x.db"
    con = sqlite3.connect(db)
    try:
        con.execute('CREATE TABLE t ("1000" TEXT, "20002" TEXT)')
        con.execute('INSERT INTO t VALUES (?, ?)', ("123456789", "张三"))
        con.execute('INSERT INTO t VALUES (?, ?)', ("u_abc", "李四"))
        con.execute('INSERT INTO t VALUES (?, ?)', (None, None))
        con.commit()
        stats = pc.column_stats(con, "t", 10)
        assert stats["1000"] == {"digits": 1, "uid": 1, "null": 1}
        assert stats["20002"] == {"text": 2, "null": 1}
        text = repr(stats)
        assert "123456789" not in text and "张三" not in text and "u_abc" not in text
    finally:
        con.close()


def test_describe_lists_tables_and_columns(tmp_path):
    db = tmp_path / "y.db"
    con = sqlite3.connect(db)
    try:
        con.execute('CREATE TABLE "group_member3" ("60001" INTEGER, "64005" TEXT)')
        con.execute('INSERT INTO "group_member3" VALUES (1, "nick")')
        con.commit()
        out = pc.describe(con, 10)
        assert "group_member3" in out
        assert out["group_member3"]["60001"] == {"number": 1}
        assert out["group_member3"]["64005"] == {"text": 1}
        assert "nick" not in repr(out)
    finally:
        con.close()


def test_probe_formats_cli_runs_on_synthetic_db(tmp_path, capsys):
    from tests.helpers import synth_db as sd
    db = sd.create_msg_db(tmp_path / "nt_msg.db",
                          c2c=[sd.text_row(ts=1, text="秘密内容")])
    assert pf.main(["--db", str(db), "--limit", "10"]) == 0
    out = capsys.readouterr().out
    assert "40011=2" in out
    assert "45101" in out
    assert "秘密内容" not in out
