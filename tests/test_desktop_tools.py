import time
from pathlib import Path
from tests.test_electron_backend import backend


def completed(value,job):
    end = time.monotonic()+15
    while time.monotonic()<end:
        state = value.dispatch("tool",dict(action="queueState",job=job["id"]))
        if not state["running"]:
            assert state["error"] is None,state
            return state["result"]
        time.sleep(.005)
    raise AssertionError("任务未完成")


def test_batch_archive_library_and_offline_browsing_are_in_process(tmp_path,monkeypatch):
    value = backend(tmp_path,monkeypatch)
    chat = value.infos[0].chat_id
    try:
        result = completed(value,value.dispatch("tool",dict(action="batch",chats=[chat],formats=["txt"],
            out=str(tmp_path/"batch"),manifest=True,remember=True)))
        assert result["tasks"][0]["status"]=="done"
        assert (tmp_path/"batch/QQ聊天记录导出/manifest.json").is_file()
        archive = tmp_path/"roundtrip.qqarchive"
        created = completed(value,value.dispatch("tool",dict(action="archiveCreate",chats=[chat],
            path=str(archive),password="synthetic-passphrase")))
        assert created["encrypted"] and created["messages"]==101
        verified = completed(value,value.dispatch("tool",dict(action="archiveVerify",path=str(archive),password="synthetic-passphrase")))
        assert "校验通过" in verified["message"]
        imported = completed(value,value.dispatch("tool",dict(action="libraryImport",path=str(archive),password="synthetic-passphrase")))
        assert imported["added"]==101
        accounts = value.dispatch("tool",dict(action="libraryAccounts"))
        mounted = value.dispatch("tool",dict(action="libraryOpen",owner=accounts[0]["owner"]))
        assert mounted["state"]["account"]["name"]=="本机资料库"
        assert value.dispatch("messages",dict(chat=chat,offset=50,size=1))["messages"][0]["index"]==50
        assert value.search("synthetic 50",chat)[0]["index"]==50
        reopened = completed(value,value.dispatch("tool",dict(action="archiveOpen",path=str(archive),password="synthetic-passphrase")))
        assert reopened["state"]["account"]["name"]=="离线归档"
        assert value.dispatch("messages",dict(chat=chat,size=1))["count"]==101
    finally:
        value.close()


def test_incremental_queue_resume_settings_missing_and_search_export(tmp_path,monkeypatch):
    from qqexport.search import Query
    value = backend(tmp_path,monkeypatch)
    chat = value.infos[0].chat_id
    try:
        options=dict(action="batch",chats=[chat],formats=["txt"],out=str(tmp_path/"incremental"),incremental=True,remember=True)
        first = completed(value,value.dispatch("tool",options))
        assert first["tasks"][0]["messages_added"]==101
        second = completed(value,value.dispatch("tool",options))
        assert second["tasks"][0]["messages_added"]==0
        checkpoint = next((tmp_path/"home/jobs").glob("*.json"))
        resumed = completed(value,value.dispatch("tool",dict(action="queueResume",path=str(checkpoint))))
        assert resumed["tasks"][0]["status"]=="done"
        result = completed(value,value.dispatch("tool",dict(action="searchExport",query=dict(text="synthetic 50",chat_id=chat),
            formats=["txt"],out=str(tmp_path/"search-export"))))
        assert "1 条" in result["message"]
        assert value.dispatch("tool",dict(action="missing",chat=chat))["rows"]==[]
        preferences = value.dispatch("tool",dict(action="settingsSave",formats=["txt"],density="compact",thumbnail_mb=16))
        assert preferences["density"]=="compact" and preferences["thumbnail_mb"]==16
        value.dispatch("tool",dict(action="searchClear"))
        assert value.tools.search is None
    finally:
        value.close()


def test_cancel_controls_do_not_wait_for_job_repository_lock(tmp_path,monkeypatch):
    import threading
    value = backend(tmp_path,monkeypatch)
    started = threading.Event()
    def work(cancel,progress):
        started.set()
        assert cancel.wait(3)
        return {"message":"cancelled"}
    job = value.tools.start("合成慢任务",work)
    try:
        assert started.wait(1)
        start = time.monotonic()
        status = value.dispatch("tool",dict(action="queueCancel"))
        assert time.monotonic()-start<.5
        assert status["id"]==job["id"]
        assert "已取消" in completed(value,job)["message"]
    finally:
        value.close()


def test_schedule_history_cache_and_library_controls(tmp_path,monkeypatch):
    from qqexport.core.discover import QQAccount
    value = backend(tmp_path,monkeypatch)
    value.source.meta["account"] = QQAccount("12345",tmp_path,tmp_path/"nt_db",tmp_path/"nt_data")
    chat = value.infos[0].chat_id
    try:
        plan = tmp_path/"daily.xml"
        result = value.dispatch("tool",dict(action="schedule",chats=[chat],formats=["txt"],out=str(tmp_path/"scheduled"),path=str(plan),clock="20:00"))
        assert plan.is_file() and plan.with_suffix(".plan.json").is_file()
        assert "<Enabled>false</Enabled>" in plan.read_text(encoding="utf-16")
        assert value.dispatch("tool",dict(action="searchHistory",text="synthetic"))==["synthetic"]
        assert value.dispatch("tool",dict(action="searchHistory",clear=True))==[]
        assert "已清理" in value.dispatch("tool",dict(action="cacheClear"))["message"]
        assert "已清空" in value.dispatch("tool",dict(action="libraryClear"))["message"]
    finally:
        value.close()
