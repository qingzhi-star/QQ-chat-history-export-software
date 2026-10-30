"""T1：合成 SQLite 库工厂的测试。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from tests.helpers import synth_db as sd


def qi(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=DELETE")
    return con


def test_msg_db_quick_check_and_columns(tmp_path):
    db = tmp_path / "nt_msg.db"
    sd.create_msg_db(db, c2c=[sd.text_row(ts=100, text="a")],
                     group=[sd.text_row(ts=200, text="b", group="123")])
    con = qi(db)
    try:
        assert con.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        cols = [r[1] for r in con.execute('PRAGMA table_info("c2c_msg_table")')]
        assert "40001" in cols and "40800" in cols
        assert con.execute('SELECT COUNT(*) FROM "c2c_msg_table"').fetchone()[0] == 1
        assert con.execute('SELECT COUNT(*) FROM "group_msg_table"').fetchone()[0] == 1
        blob = con.execute('SELECT "40800" FROM "c2c_msg_table"').fetchone()[0]
        assert isinstance(blob, bytes) and blob
    finally:
        con.close()


def test_msg_db_can_drop_column(tmp_path):
    db = tmp_path / "nt_msg.db"
    sd.create_msg_db(db, c2c=[sd.text_row(ts=1, text="x")],
                     drop_columns=["40030"])
    con = qi(db)
    try:
        cols = [r[1] for r in con.execute('PRAGMA table_info("c2c_msg_table")')]
        assert "40030" not in cols
        assert con.execute('SELECT COUNT(*) FROM "c2c_msg_table"').fetchone()[0] == 1
    finally:
        con.close()


def test_companion_plain_schemas(tmp_path):
    for kind, table in (("profile", "profile_info_v6"),
                        ("group", "group_member3"),
                        ("rich_media", "file_table")):
        db = tmp_path / f"{kind}.db"
        sd.create_companion_db(db, kind=kind)
        con = qi(db)
        try:
            assert con.execute("PRAGMA quick_check").fetchone()[0] == "ok"
            names = [r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")]
            assert table in names
        finally:
            con.close()


def test_padding_helper_makes_page_multiple(tmp_path):
    p = tmp_path / "x.bin"
    p.write_bytes(b"abc")
    sd.pad_to_page_multiple(p)
    assert p.stat().st_size % 4096 == 0
    assert p.read_bytes()[:3] == b"abc"
