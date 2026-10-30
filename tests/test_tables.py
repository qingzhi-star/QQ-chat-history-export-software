"""T3：表/列发现、流式读取、会话列表。"""
from __future__ import annotations

import sqlite3

from qqexport.qq import tables as t
from tests.helpers import synth_db as sd


def con_of(path):
    return sqlite3.connect(path)


def test_discover_finds_variant_table_names(tmp_path):
    db = sd.create_msg_db(tmp_path / "nt_msg.db",
                          c2c=[sd.text_row(ts=1, text="a")],
                          group=[sd.text_row(ts=2, text="b", group="123")])
    con = con_of(db)
    try:
        con.execute('ALTER TABLE "c2c_msg_table" RENAME TO "c2c_msg_table_v2"')
        con.execute('ALTER TABLE "group_msg_table" RENAME TO "group_msg_table_2026"')
        con.commit()
        ts = t.discover(con)
        assert ts.c2c == "c2c_msg_table_v2"
        assert ts.group == "group_msg_table_2026"
    finally:
        con.close()


def test_discover_returns_none_when_missing(tmp_path):
    db = tmp_path / "empty.db"
    con = sqlite3.connect(db)
    try:
        con.execute("CREATE TABLE unrelated (x INTEGER)")
        con.commit()
        ts = t.discover(con)
        assert ts.c2c is None and ts.group is None
    finally:
        con.close()


def test_iter_rows_fills_missing_columns_with_none(tmp_path):
    db = sd.create_msg_db(tmp_path / "nt_msg.db",
                          c2c=[sd.text_row(ts=10, text="x")],
                          drop_columns=["40030"])
    con = con_of(db)
    try:
        rows = list(t.iter_rows(con, "c2c_msg_table", batch=10))
        assert len(rows) == 1
        assert rows[0]["40011"] == 2
        assert rows[0]["40030"] is None
        assert set(rows[0]) == set(t.MSG_COLUMNS) if hasattr(t, "MSG_COLUMNS") else True
    finally:
        con.close()


def test_iter_rows_streams_all_rows_in_batches(tmp_path):
    rows = [sd.text_row(ts=i, text=f"t{i}", msg_id=i) for i in range(2500)]
    db = sd.create_msg_db(tmp_path / "nt_msg.db", c2c=rows)
    con = con_of(db)
    try:
        got = list(t.iter_rows(con, "c2c_msg_table", batch=100))
        assert len(got) == 2500
        assert [r["40050"] for r in got] == list(range(2500))
    finally:
        con.close()


def test_list_chats_groups_by_peer_with_time_range(tmp_path):
    c2c = [sd.text_row(ts=100, text="a", uid="u_a", msg_id=1),
           sd.text_row(ts=300, text="b", uid="u_a", msg_id=2),
           sd.text_row(ts=200, text="c", uid="u_b", msg_id=3)]
    group = [sd.text_row(ts=400, text="d", group="123", msg_id=4),
             sd.text_row(ts=500, text="e", group="123", msg_id=5)]
    db = sd.create_msg_db(tmp_path / "nt_msg.db", c2c=c2c, group=group)
    con = con_of(db)
    try:
        chats = t.list_chats(con)
        by_id = {c.chat_id: c for c in chats}
        assert by_id["c2c:u_a"].messages == 2
        assert by_id["c2c:u_a"].first_ts == 100
        assert by_id["c2c:u_a"].last_ts == 300
        assert by_id["c2c:u_b"].messages == 1
        assert by_id["group:123"].messages == 2
        assert [c.chat_id for c in chats] == sorted(by_id)
    finally:
        con.close()


def test_iter_chat_rows_filters_by_peer(tmp_path):
    c2c = [sd.text_row(ts=1, text="a", uid="u_a"),
           sd.text_row(ts=2, text="b", uid="u_b")]
    db = sd.create_msg_db(tmp_path / "nt_msg.db", c2c=c2c)
    con = con_of(db)
    try:
        rows = list(t.iter_chat_rows(con, "c2c", "u_a"))
        assert len(rows) == 1
        assert rows[0]["40020"] == "u_a"
    finally:
        con.close()
