"""新桌面端协议使用真实合成数据库验证，避免只测假界面。"""
import json
import subprocess
import sys
from qqexport.electron_backend import DesktopBackend
from qqexport.repository import MessageRepository
from tests.test_repository import source


def backend(tmp_path,monkeypatch):
    monkeypatch.setenv("QQEXPORT_HOME",str(tmp_path/"home"))
    value = DesktopBackend()
    value.source = source(tmp_path,count=101)
    value.repo = MessageRepository(value.source)
    value.infos,value.metas = value.repo.sidebar()
    return value


def test_desktop_paging_selection_search_and_export(tmp_path,monkeypatch):
    value = backend(tmp_path,monkeypatch)
    try:
        state = value.state()
        chat = state["chats"][0]["id"]
        assert state["chats"][0]["count"]==101
        page = value.dispatch("messages",dict(chat=chat,offset=48,size=10))
        assert [item["index"] for item in page["messages"]]==list(range(48,58))
        assert value.dispatch("range",dict(chat=chat,start=0,end=0))==[0,1,2]
        hits = value.dispatch("search",dict(chat=chat,query="synthetic 50"))
        assert hits[0]["index"]==50
        report = value.dispatch("export",dict(chat=chat,formats=["txt"],out=str(tmp_path/"out"),selected=[48,49]))
        assert report["messages"]==2 and report["failed"]==0
        output = next((tmp_path/"out").rglob("chat.txt")).read_text(encoding="utf-8-sig")
        assert "synthetic 48" in output and "synthetic 49" in output and "synthetic 50" not in output
    finally:
        value.close()


def test_desktop_media_tokens_are_scoped_and_failures_keep_session(tmp_path,monkeypatch):
    import pytest
    from qqexport.errors import UserError
    value = backend(tmp_path,monkeypatch)
    old = value.source
    try:
        assert value.media("not-a-registered-token") is None
        with pytest.raises(UserError):
            value.open_media("not-a-registered-token")
        with pytest.raises(UserError):
            value.switch("11111111")
        assert value.source is old
        assert value.repo.count(value.infos[0].chat_id)==101
        with pytest.raises(UserError):
            value.dispatch("__dict__",{})
    finally:
        value.close()


def test_desktop_pipe_protocol_and_clean_exit(tmp_path,monkeypatch):
    import os
    monkeypatch.setenv("QQEXPORT_HOME",str(tmp_path/"home"))
    env = dict(os.environ,QILU_DEMO="1",PYTHONIOENCODING="utf-8")
    result = subprocess.run([sys.executable,"-m","qqexport.electron_backend"],
        input=json.dumps(dict(id=1,method="boot",params={}))+'\n'+json.dumps(dict(id=2,method="unsupported",params={}))+'\n',
        text=True,encoding="utf-8",capture_output=True,timeout=20,env=env)
    assert result.returncode==0
    rows = {row["id"]:row for line in result.stdout.splitlines() if (row:=json.loads(line))}
    assert rows[1]["result"]["chats"][0]["count"]==100000
    assert "error" in rows[2]
