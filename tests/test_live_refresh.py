"""实时快照变化只更新变化的会话，保留滚动、当前会话和导出选择。"""
import time
import threading
from qqexport.core import discover,keystore
from qqexport.export.session import ExportSource
from qqexport.product_gui import ProductApp
from qqexport.qq import contacts,media
from qqexport.tk_runtime import create_root
from tests.helpers import synth_db as sd


def test_live_sort_and_append_keep_current_preview_widgets(tmp_path,monkeypatch):
    monkeypatch.setenv("QQEXPORT_HOME",str(tmp_path/"home"))
    account = discover.QQAccount("12345",tmp_path,tmp_path/"nt_db",tmp_path/"nt_data")
    version,opened = [0],[]
    gate = threading.Event()
    gate.set()
    def open_source(account,key,**kwargs):
        assert gate.wait(3)
        directory = tmp_path/str(len(opened))
        directory.mkdir()
        rows = [sd.text_row(msg_id=1,ts=100,uid="u_a",text="合成甲"),
                sd.text_row(msg_id=2,ts=200,uid="u_b",text="合成乙")]
        if version[0]>=1:
            rows.append(sd.text_row(msg_id=3,ts=300,uid="u_b",text="合成乙新消息"))
        if version[0]>=2:
            rows.append(sd.text_row(msg_id=4,ts=400,uid="u_a",text="合成甲新消息"))
        db = sd.create_msg_db(directory/"messages.db",c2c=rows)
        src = ExportSource.from_parts(db,contacts.Contacts.load(None,None,account_uin="12345"),
                                      media.build_index(directory/"media"),"12345")
        src.meta["account"] = account
        opened.append(src)
        return src
    monkeypatch.setattr(ExportSource,"open",staticmethod(open_source))
    monkeypatch.setattr(keystore.KeyStore,"load",lambda self,uin:b"synthetic")
    class SyntheticApp(ProductApp):
        def _setup_logging(self):
            pass
        def _load_chats(self):
            pass
        def _schedule_watch(self):
            pass
    root = create_root()
    app = SyntheticApp(root)
    def wait(predicate):
        end = time.monotonic()+5
        while time.monotonic()<end:
            root.update()
            if predicate():
                return
            time.sleep(.005)
        assert predicate()
    def refresh():
        old = app.session
        app._open_account(account,refresh=True)
        wait(lambda:app.session is not old)
        wait(lambda:not app._busy)
    try:
        app._open_account(account)
        wait(lambda:app.session is not None)
        assert app.infos[0].chat_id=="c2c:u_b"
        app._select_chat(1)
        wait(lambda:bool(app.virtual.pages) and not app._busy)
        message = app.virtual.message(0)
        row = app.virtual.rows[0][0]
        version[0] = 1
        refresh()
        root.update()
        assert app.current_chat_id=="c2c:u_a"
        assert app.virtual.message(0) is message
        assert app.virtual.rows[0][0] is row
        version[0] = 2
        refresh()
        wait(lambda:app.virtual.message(1) is not None)
        assert app.infos[0].chat_id=="c2c:u_a"
        assert app.infos[app.selected_idx].chat_id=="c2c:u_a"
        assert app.virtual.count==2
        assert app.virtual.rows[0][0] is row
        assert app.virtual.message(0) is message
        assert app.virtual.anchor()[0]==0
        gate.clear()
        old = app.session
        app._open_account(account,refresh=True)
        app._open_export()
        app._toggle_check(0)
        gate.set()
        wait(lambda:app.tasks.idle())
        root.update()
        assert app.session is old and not old.retired
        assert app.export_mode and app.selected_msgs=={0}
    finally:
        gate.set()
        app.close()
