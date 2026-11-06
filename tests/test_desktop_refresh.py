"""运行中写库、账号切换、断网请求使用合成数据和可控屏障复现。"""
from concurrent.futures import ThreadPoolExecutor
import json
import os
import subprocess
import sys
import threading
import time
from unittest.mock import Mock

import pytest

from qqexport.core import decrypt, snapshot
from qqexport.core.discover import QQAccount
from qqexport.electron_backend import DesktopBackend
from qqexport.errors import UserError
from qqexport.export.session import ExportSource
from tests.test_electron_backend import backend
from tests.test_repository import source


def account(tmp_path,uin="12345"):
    root=tmp_path/uin
    return QQAccount(uin,root,root/"nt_db",root/"nt_data")


def active_backend(tmp_path,monkeypatch):
    value=backend(tmp_path,monkeypatch)
    value.account=account(tmp_path)
    value.accounts={value.account.uin:value.account}
    value.stamp="previous snapshot"
    monkeypatch.setattr("qqexport.electron_backend.account_key",lambda *args,**kwargs:b"synthetic")
    return value


def test_busy_refresh_retains_readable_source_and_recovers_after_backoff(tmp_path,monkeypatch):
    value=active_backend(tmp_path,monkeypatch)
    old=value.source
    clock=[100.]
    monkeypatch.setattr("qqexport.electron_backend.time.monotonic",lambda:clock[0])
    prepare=Mock(side_effect=decrypt.DatabaseBusyError("synthetic busy"))
    monkeypatch.setattr(value,"_prepare",prepare)
    try:
        for delay in (2,4,8,16,16):
            assert value.dispatch("refresh",{}) is None
            assert value.source is old and value.generation==0
            assert value.dispatch("messages",dict(chat="c2c:u_a",size=1))["messages"]
            calls=prepare.call_count
            assert value.dispatch("refresh",{}) is None and prepare.call_count==calls
            clock[0]+=delay
        new_root=tmp_path/"new";new_root.mkdir()
        fresh=source(new_root,7)
        monkeypatch.setattr(ExportSource,"open",lambda *args,**kwargs:fresh)
        monkeypatch.setattr(value,"_prepare",DesktopBackend._prepare)
        result=value.dispatch("refresh",{})
        assert result["generation"]==1 and result["chats"][0]["count"]==7
        assert value._refresh_failures==0
    finally:
        value.close()


def test_refresh_does_not_block_chat_reads_or_overwrite_new_account(tmp_path,monkeypatch):
    value=active_backend(tmp_path,monkeypatch)
    other=account(tmp_path,"11111111");value.accounts[other.uin]=other
    candidate_root=tmp_path/"candidate";candidate_root.mkdir()
    stale=source(candidate_root,9);stale.close=Mock()
    other_root=tmp_path/"other";other_root.mkdir()
    fresh=source(other_root,7);fresh.account_uin=other.uin
    entered,release=threading.Event(),threading.Event()
    def open_source(selected,*args,**kwargs):
        if selected==other:
            return fresh
        entered.set()
        assert release.wait(5)
        return stale
    monkeypatch.setattr(ExportSource,"open",open_source)
    try:
        with ThreadPoolExecutor(3) as pool:
            pending=pool.submit(value.dispatch,"refresh",{})
            assert entered.wait(2)
            # 刷新停在屏障处时，聊天页和显式账号切换均应独立完成。
            try:
                page=pool.submit(value.dispatch,"messages",dict(chat="c2c:u_a",size=3)).result(timeout=1)
                assert len(page["messages"])==3
                switched=pool.submit(value.dispatch,"switch",dict(uin=other.uin)).result(timeout=1)
                assert switched["account"]["uin"]==other.uin
            finally:
                release.set()
            assert pending.result(timeout=2) is None
        assert value.source is fresh and value.repo.count("c2c:u_a")==7
        stale.close.assert_called_once()
    finally:
        release.set();value.close()


def test_switch_retries_only_busy_snapshots_without_reacquiring_key(tmp_path,monkeypatch):
    value=active_backend(tmp_path,monkeypatch)
    fresh_root=tmp_path/"fresh";fresh_root.mkdir()
    fresh=source(fresh_root,7)
    open_source=Mock(side_effect=[decrypt.DatabaseBusyError("busy"),decrypt.DatabaseBusyError("busy"),fresh])
    monkeypatch.setattr(ExportSource,"open",open_source)
    account_avatar=value.register("account-avatar",value.account.uin,value.account.uin)
    old_media=value.register("media","synthetic","old-media")
    sleeps=[];monkeypatch.setattr("qqexport.electron_backend.time.sleep",sleeps.append)
    try:
        assert value.switch(value.account.uin)["chats"][0]["count"]==7
        assert open_source.call_count==3 and sleeps==[.2,.4]
        assert account_avatar in value.resources and old_media not in value.resources
        # 再次选中未变化的当前账号，直接复用已验证快照。
        value.switch(value.account.uin)
        assert open_source.call_count==3
    finally:
        value.close()


def test_invalid_snapshot_is_not_silently_treated_as_busy(tmp_path,monkeypatch):
    value=active_backend(tmp_path,monkeypatch)
    old=value.source
    prepare=Mock(side_effect=decrypt.DecryptError("synthetic invalid key"))
    monkeypatch.setattr(value,"_prepare",prepare)
    try:
        with pytest.raises(UserError,match="本机记录暂时无法读取"):
            value.refresh()
        assert value.source is old and value.generation==0
        with pytest.raises(UserError,match="未能读取"):
            value.switch(value.account.uin)
        assert prepare.call_count==2  # 真正解密失败不盲目重复解密/扫描。
    finally:
        value.close()


def test_decrypt_preserves_busy_classification(tmp_path,monkeypatch):
    selected=account(tmp_path)
    selected.db_dir.mkdir(parents=True);selected.msg_db.write_bytes(b"synthetic")
    monkeypatch.setattr(snapshot,"snapshot",Mock(side_effect=snapshot.SnapshotBusyError("busy")))
    with pytest.raises(decrypt.DatabaseBusyError):
        decrypt.decrypt_database(selected,b"synthetic",dst_dir=tmp_path/"out")


def test_offline_media_timeouts_cannot_starve_message_requests(tmp_path,monkeypatch):
    monkeypatch.setenv("QQEXPORT_HOME",str(tmp_path/"home"))
    script="""import time
from qqexport import electron_backend as backend
def offline_media(self, token):
    time.sleep(2)
    return None
backend.DesktopBackend.media=offline_media
backend.main()
"""
    process=subprocess.Popen([sys.executable,"-c",script],stdin=subprocess.PIPE,stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,text=True,encoding="utf-8",env=dict(os.environ,QILU_DEMO="1"))
    def send(identifier,method,params):
        process.stdin.write(json.dumps(dict(id=identifier,method=method,params=params))+"\n")
        process.stdin.flush()
    try:
        send(1,"boot",{})
        state=json.loads(process.stdout.readline())["result"]
        for identifier in range(2,6):
            send(identifier,"media",dict(token="offline-avatar"))
        start=time.monotonic()
        send(6,"messages",dict(chat=state["chats"][0]["id"],size=3))
        response=json.loads(process.stdout.readline())
        assert response["id"]==6 and len(response["result"]["messages"])==3
        assert time.monotonic()-start<1
    finally:
        process.stdin.close();process.wait(timeout=10)
        process.stdout.close();process.stderr.close()


def test_offline_cached_key_opens_and_exports_real_encrypted_database(tmp_path,monkeypatch):
    import hashlib
    from qqexport.core import keystore,keyscan,discover
    from tests.helpers import synth_db
    from tests.helpers.encrypted_sqlite import encrypt_fixture
    monkeypatch.setenv("QQEXPORT_HOME",str(tmp_path/"home"))
    selected=account(tmp_path)
    rows=[synth_db.text_row(ts=i+1,msg_id=i+1,text=f"offline synthetic {i}",uid="u_a") for i in range(12)]
    synth_db.create_msg_db(selected.msg_db,c2c=rows)
    key=b"#8FakeKeyForTest"
    encrypt_fixture(selected.msg_db,key)
    before=hashlib.sha256(selected.msg_db.read_bytes()).digest()
    store=Mock();store.load.return_value=key
    monkeypatch.setattr(keystore,"KeyStore",lambda:store)
    monkeypatch.setattr(keyscan,"scan_key",lambda *args:pytest.fail("offline must not read QQ process"))
    monkeypatch.setattr("urllib.request.urlopen",lambda *args,**kwargs:pytest.fail("offline export must not request network"))
    monkeypatch.setattr(discover,"discover_accounts",lambda **kwargs:[selected])
    # 四项真实处理必须同时就绪：串行实现会在第一个屏障超时，不能伪造加速。
    from qqexport.core import companions
    from qqexport.qq import media
    barrier=threading.Barrier(4)
    real_decrypt,real_companion,real_index=decrypt.decrypt_database,companions.decrypt_companion,media.build_index
    def parallel(fn):
        def run(*args,**kwargs):
            barrier.wait(timeout=5)
            return fn(*args,**kwargs)
        return run
    monkeypatch.setattr(decrypt,"decrypt_database",parallel(real_decrypt))
    monkeypatch.setattr(companions,"decrypt_companion",parallel(real_companion))
    monkeypatch.setattr(media,"build_index",parallel(real_index))
    # 账号显示资料读取不参与源准备屏障，只用合成身份。
    from qqexport.account_panel import Identity
    monkeypatch.setattr("qqexport.electron_backend.read_identity",lambda account,**kwargs:Identity(account.uin,"合成账号"))
    value=DesktopBackend()
    try:
        state=value.dispatch("boot",{})
        assert state["chats"][0]["count"]==12
        assert len(value.dispatch("messages",dict(chat="c2c:u_a"))["messages"])==12
        result=value.dispatch("export",dict(chat="c2c:u_a",formats=["txt","html"],out=str(tmp_path/"out"),all_messages=True))
        assert result["messages"]==12 and result["failed"]==0
        assert len(list((tmp_path/"out").rglob("chat.txt")))==1
        assert len(list((tmp_path/"out").rglob("chat.html")))==1
        store.save.assert_not_called()
    finally:
        value.close()
    assert hashlib.sha256(selected.msg_db.read_bytes()).digest()==before


def test_avatar_requests_require_separate_permission(tmp_path,monkeypatch):
    monkeypatch.delenv("QILU_ALLOW_AVATARS",raising=False)
    value=backend(tmp_path,monkeypatch)
    fetch=Mock(return_value=None)
    monkeypatch.setattr("qqexport.electron_backend.avatars.fetch_avatar",fetch)
    try:
        token=value.portrait("12345")
        assert value.media(token) is None
        fetch.assert_not_called()
        value.allow_avatars=True
        assert value.media(token) is None
        fetch.assert_called_once_with("12345")
    finally:
        value.close()
