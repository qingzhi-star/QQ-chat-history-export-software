from pathlib import Path
import hashlib

from qqexport.repository import MessageRepository
from qqexport.export.session import ExportSource
from qqexport.qq import contacts, media
from tests.helpers import synth_db as sd


def source(tmp_path, count=101):
    rows = [sd.text_row(ts=i//3, msg_id=i+1, text=f"synthetic {i}", uid="u_a") for i in range(count)]
    db = sd.create_msg_db(tmp_path / "messages.db", c2c=rows)
    return ExportSource.from_parts(db, contacts.Contacts.load(None, None, account_uin="12345"),
                                   media.build_index(tmp_path / "media"), "12345")


def test_cursor_pages_are_ordered_complete_and_source_readonly(tmp_path):
    src = source(tmp_path)
    before = hashlib.sha256(src.msg_db.read_bytes()).digest()
    repo = MessageRepository(src)
    cursor, ids = None, []
    while True:
        page = repo.page("c2c:u_a", after=cursor, limit=7)
        assert len(page.messages) <= 7
        ids.extend(m.msg_id for m in page.messages)
        cursor = page.cursor
        if page.exhausted:
            break
    assert ids == list(range(1, 102))
    assert repo.count("c2c:u_a") == 101
    assert repo.window("c2c:u_a", 98, 5)[0][1].msg_id == 99
    assert [m.msg_id for m in repo.page("c2c:u_a", limit=3, reverse=True).messages] == [99,100,101]
    assert repo.position("c2c:u_a", 99) == 98
    assert repo.ids_for_indices("c2c:u_a", {0, 50, 100}) == {1, 51, 101}
    assert repo.indices_in_range("c2c:u_a", 0, 0) == {0,1,2}
    repo.close()
    assert hashlib.sha256(src.msg_db.read_bytes()).digest() == before


def test_repository_bounds_and_empty(tmp_path):
    import pytest
    repo = MessageRepository(source(tmp_path, 0))
    assert repo.page("c2c:u_a").exhausted
    assert list(repo.iter_messages("c2c:u_a")) == []
    with pytest.raises(ValueError):
        repo.window("c2c:u_a", -1)
    with pytest.raises(ValueError):
        repo.page("c2c:u_a", limit=100000)
    repo.close()


def test_sidebar_bulk_summary_is_sorted_and_does_not_rescan_source(tmp_path,monkeypatch):
    src = source(tmp_path,20)
    import sqlite3
    with sqlite3.connect(src.msg_db) as con:
        con.execute('UPDATE c2c_msg_table SET "40021"=CASE WHEN "40001"%2=0 THEN \'u_b\' ELSE \'u_a\' END')
    monkeypatch.setattr(src,"chats",lambda:(_ for _ in ()).throw(AssertionError("per-chat source scan")))
    repo = MessageRepository(src)
    try:
        infos,metas = repo.sidebar()
        assert [i.chat_id for i in infos]==["c2c:u_a","c2c:u_b"]  # 同时刻按 ID 确定排序
        assert len(metas)==2
        assert all(repo.count(info.chat_id)==10 for info in infos)
        revision = repo.chat_revision(infos[0].chat_id)
        assert revision and repo.sidebar()==(infos,metas)
    finally:
        repo.close()
