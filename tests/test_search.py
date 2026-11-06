import threading
import time
import pytest

from qqexport.search import SearchIndex,Query,SearchError,ResultSource
from qqexport.repository import MessageRepository
from tests.test_repository import source


@pytest.mark.parametrize("fts",[True,False])
def test_memory_search_filters_and_exact(tmp_path,fts):
    repo = MessageRepository(source(tmp_path))
    index = SearchIndex(fts=fts)
    index.build(repo)
    assert len(index.find(Query("SYNTHETIC",chat_id="c2c:u_a",sender="u_a",kind="text",start=0,end=0))) == 3
    assert [h.msg_id for h in index.find(Query("synthetic 0",mode="exact"))] == [1]
    assert index.find(Query("%_")) == []
    assert index.find(Query("synthetic",kind="image")) == []
    assert [h.msg_id for h in index.find(Query(r"synthetic [0-2]$",mode="regex"))] == [1,2,3]
    assert all(not row[2] for row in index.con.execute("PRAGMA database_list"))
    result = ResultSource(repo,index.find(Query("synthetic 0",mode="exact")))
    assert [m.msg_id for m in result.iter_messages("c2c:u_a")] == [1]
    index.clear()
    with pytest.raises(SearchError,match="索引"):
        index.find(Query("synthetic"))
    index.close()
    repo.close()


def test_search_keyset_visits_every_result_without_duplicates(tmp_path):
    repo = MessageRepository(source(tmp_path,2101))
    index = SearchIndex()
    index.build(repo)
    hits = list(index.iter_hits(Query("synthetic")))
    assert [h.msg_id for h in hits] == list(range(1,2102))
    index.close()
    repo.close()


def test_regex_has_real_timeout_and_invalid_pattern_error():
    index = SearchIndex(fts=False)
    body = "a"*10000+"!"
    index.con.execute("INSERT INTO docs VALUES (?,?,?,?,?,?,?,?,?,?)",
                      ("c2c:u_a",1,1,"synthetic",None,None,"|text|",body,body,0))
    index.ready = True
    start = time.monotonic()
    with pytest.raises(SearchError,match="超时"):
        index.find(Query("(a|aa)+$",mode="regex"),timeout=.005)
    assert time.monotonic()-start<1
    with pytest.raises(SearchError,match="无效"):
        index.find(Query("[",mode="regex"))
    index.close()


def test_cancelled_index_never_exposes_partial_results(tmp_path):
    repo = MessageRepository(source(tmp_path))
    index = SearchIndex()
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(SearchError,match="取消"):
        index.build(repo,cancel=cancel)
    assert not index.ready
    index.close()
    repo.close()


def test_chinese_and_literal_wildcards_in_fts():
    index = SearchIndex()
    body = "合成中文测试 %_ 字符"
    index.con.execute("INSERT INTO docs VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("c2c:u_a",1,1,"synthetic",None,None,"|text|",body,body.casefold(),0))
    if index.fts:
        index.con.execute("INSERT INTO words(words) VALUES('rebuild')")
    index.ready = True
    assert len(index.find(Query("中文"))) == 1
    assert len(index.find(Query("中文测试"))) == 1
    assert len(index.find(Query("%_"))) == 1
    index.close()


def test_cli_search_uses_same_index_and_closes_source(tmp_path,monkeypatch,capsys):
    from qqexport import cli
    src = source(tmp_path)
    closed = []
    monkeypatch.setattr(src,"close",lambda:closed.append(True))
    monkeypatch.setattr(cli,"_open_source",lambda args:src)
    assert cli.main(["search","--query","synthetic 0","--match","exact"]) == 0
    assert "synthetic 0" in capsys.readouterr().out
    assert closed == [True]
