"""合成本机账号选择和切换，不读取真实 QQ 或密钥。"""
import time
import threading
import tkinter as tk
from tkinter import ttk

from qqexport.core import discover,keystore
from qqexport.export.session import ExportSource
from qqexport.product_gui import ProductApp
from qqexport.tk_runtime import create_root
from tests.test_repository import source
from qqexport.account_access import sqlcipher,keyscan
from qqexport.account_panel import Identity


def test_profile_button_selects_switches_and_cancels(tmp_path,monkeypatch):
    monkeypatch.setenv("QQEXPORT_HOME",str(tmp_path/"home"))
    accounts = [discover.QQAccount(uin,tmp_path/uin,tmp_path/uin/"nt_db",tmp_path/uin/"nt_data")
                for uin in ("12345","11111111")]
    for account in accounts:
        account.data_dir.mkdir()
    monkeypatch.setattr(discover,"discover_accounts",lambda **kwargs:accounts)
    monkeypatch.setattr(keystore.KeyStore,"load",lambda self,uin:b"synthetic")
    monkeypatch.setattr(sqlcipher,"read_header_and_page1",lambda path:b"synthetic header")
    monkeypatch.setattr(sqlcipher,"detect_params",lambda candidate,header:object())
    monkeypatch.setattr("qqexport.account_panel.read_identity",lambda account:Identity(account.uin,"合成昵称"))
    opened = []
    def open_source(account,key):
        directory = account.data_dir/str(len(opened))
        directory.mkdir()
        opened.append(account.uin)
        src = source(directory,count=3 if account==accounts[0] else 5)
        src.account_uin = account.uin
        src.meta["account"] = account
        return src
    monkeypatch.setattr(ExportSource,"open",staticmethod(open_source))

    class SyntheticApp(ProductApp):
        def _setup_logging(self):
            pass
        def _load_chats(self):
            pass

    root = create_root()
    app = SyntheticApp(root)
    def wait(predicate):
        end = time.monotonic()+5
        while time.monotonic()<end:
            root.update()
            if predicate():
                return
            time.sleep(.01)
        assert predicate()
    def chooser():
        return app.accounts_panel.popup
    def choose(index):
        app.account_button.invoke()
        wait(lambda:chooser() is not None)
        assert set(app.accounts_panel.rows)=={a.uin for a in accounts}
        app.accounts_panel.choose(accounts[index])
        wait(lambda:app.uin==accounts[index].uin and app.session is not None)
    try:
        root.update()
        assert app.account_button.winfo_rootx()<app.export_btn.winfo_rootx()
        assert app.account_button.winfo_rooty()<app.canvas.winfo_rooty()
        assert not app.count_label.winfo_ismapped()
        assert root.title()=="绮录QQ聊天记录导出软件"
        choose(0)
        app._select_chat(0)
        wait(lambda:app.virtual.count==3 and not app._busy)
        app._open_export()
        app._toggle_check(0)
        app.batch_selected.add(app.current_chat_id)
        old_session = app.session
        choose(1)
        assert old_session.retired
        assert not app.export_mode and not app.selected_msgs and not app.batch_selected
        assert app.current_chat_id is None and app.virtual.count==0
        assert app.title_label.cget("text")=="选择左侧会话查看内容"
        assert "disabled" in app.export_btn.state()
        app._select_chat(0)
        wait(lambda:app.virtual.count==5 and not app._busy)
        current = app.session
        app.account_button.invoke()
        wait(lambda:chooser() is not None)
        app.accounts_panel.close()
        assert app.session is current and app.uin==accounts[1].uin
        assert app.preferences.last_account==accounts[1].uin
        monkeypatch.setattr(keystore.KeyStore,"load",lambda self,uin:None)
        app._open_account(accounts[0])
        wait(lambda:"点击左上角切换账号" in app.status.cget("text"))
        assert "RuntimeError" not in app.status.cget("text")
        assert app.session is current and app.uin==accounts[1].uin
        assert app.virtual.count==5
        scans,saved = [],[]
        release = threading.Event()
        def scan(verify):
            scans.append(verify(b"synthetic"))
            assert release.wait(2)
            return b"synthetic"
        monkeypatch.setattr(keyscan,"scan_key",scan)
        monkeypatch.setattr(keystore.KeyStore,"save",lambda self,uin,key:saved.append(uin))
        responsive = []
        def heartbeat():
            if not scans:
                root.after(10,heartbeat)
                return
            responsive.append(app.account_button.instate(["disabled"]))
            release.set()
        root.after(10,heartbeat)
        choose(0)
        assert scans==[True] and saved==[accounts[0].uin]
        assert responsive==[True]
        assert "disabled" not in app.account_button.state()
        app._select_chat(0)
        wait(lambda:app.virtual.count==3 and not app._busy)
        current = app.session
        monkeypatch.setattr(keyscan,"scan_key",lambda verify:None)
        app._open_account(accounts[1],auto_key=True)
        from qqexport.account_access import AccountAccessError
        wait(lambda:AccountAccessError.MESSAGES["not_found"] in app.status.cget("text"))
        assert app.session is current and app.uin==accounts[0].uin
        assert app.virtual.count==3 and "disabled" not in app.account_button.state()
        monkeypatch.setattr(discover,"discover_accounts",lambda **kwargs:[])
        app.account_button.invoke()
        wait(lambda:app.status.cget("text")=="未找到本机账号")
        assert app.session is current
    finally:
        app.close()
