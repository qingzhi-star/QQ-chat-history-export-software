import tkinter as tk

from qqexport.export.model import Message, Segment
from qqexport.virtual_list import HeightIndex, VirtualMessages
from qqexport.tasks import DataSession
from qqexport.tk_runtime import create_root


def test_height_index_updates_and_locates():
    index = HeightIndex(100000)
    index.set(3,200)
    assert index.prefix(4) == 3*104+200
    assert index.locate(3*104+150) == 3
    assert index.locate(index.prefix(99999)) == 99999


def test_virtual_widget_pool_and_scroll_stay_bounded():
    root = create_root()
    root.geometry("800x600")
    view = None
    def request(offset,size):
        rows = [(i,Message(i,100+i,None,None,"合成",False,[Segment("text",f"synthetic {i}")]))
                for i in range(offset,min(offset+size,100000))]
        view.loaded(offset,rows)
    view = VirtualMessages(root,request,lambda i: None)
    view.pack(fill="both",expand=True)
    view.reset(100000)
    root.update()
    view.see(50000)
    root.update()
    anchor = view.anchor()
    view.selection(True,{50000})
    root.update()
    assert abs(view.anchor()[0]-anchor[0]) <= 1
    assert len(view.rows)+len(view.free) <= 60
    assert len(view.pages) <= 8
    view.selection(False,set())
    root.update()
    assert abs(view.anchor()[0]-anchor[0]) <= 1
    root.destroy()


def test_source_retirement_waits_for_last_lease():
    from types import SimpleNamespace
    closed = []
    source = SimpleNamespace(close=lambda: closed.append("source"))
    repo = SimpleNamespace(close=lambda: closed.append("repo"))
    session = DataSession(source,repo)
    one,two = session.pin(),session.pin()
    session.retire()
    assert closed == []
    one()
    assert closed == []
    two()
    assert closed == ["repo","source"]
    two()
    assert len(closed) == 2


def test_product_paging_selection_dates_and_chat_switch(tmp_path,monkeypatch):
    import time
    from qqexport.product_gui import ProductApp
    from qqexport.repository import MessageRepository
    from qqexport.core.discover import QQAccount
    from tests.test_repository import source
    src = source(tmp_path)
    monkeypatch.setenv("QQEXPORT_HOME",str(tmp_path/"home"))
    src.meta["account"] = QQAccount("12345",tmp_path,tmp_path/"nt_db",tmp_path/"nt_data")
    repo = MessageRepository(src)

    class SyntheticApp(ProductApp):
        def _setup_logging(self):
            pass
        def _load_chats(self):
            self.session = DataSession(src,repo)
            self._loaded(src,src.chats(),{}, {})

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
    try:
        app._select_chat(0)
        wait(lambda: app.virtual.count == 101 and bool(app.virtual.pages))
        app.virtual.see(50)
        root.update()
        anchor = app.virtual.anchor()
        app._open_export()
        app._toggle_all()
        root.update()
        assert len(app.selected_msgs) == 101
        assert abs(app.virtual.anchor()[0]-anchor[0])<=1
        app._apply_dates(0,0)
        wait(lambda: app.selected_msgs == {0,1,2})
        app._toggle_check(1)
        assert app.selected_msgs == {0,2}
        app._cancel_export()
        assert not app.export_mode
        info = src.chats()[0]
        app._start_preview(info)
        app._start_preview(info)
        wait(lambda: not app._busy and bool(app.virtual.pages))
        assert app.current_chat_id == "c2c:u_a"
        app._show_search()
        dialog = app._search_dialog
        dialog.text.set("synthetic 50")
        dialog.mode.set("exact")
        dialog.search(0)
        wait(lambda: len(dialog.hits)==1)
        dialog.tree.selection_set("0")
        dialog.jump()
        wait(lambda: app.virtual.highlight==50 and not app._busy)
        dialog.clear_index()
        wait(lambda: not app.session.search.ready)
        dialog.close()
    finally:
        app.close()
