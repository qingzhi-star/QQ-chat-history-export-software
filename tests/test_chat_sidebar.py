"""大量合成会话的原样控件池、滚动及实时排序。"""
from types import SimpleNamespace
from qqexport.export.session import ChatInfo
from qqexport.product_gui import ProductApp
from qqexport.tk_runtime import create_root


def test_sidebar_is_bounded_sorted_and_rebinds_recycled_rows(tmp_path,monkeypatch):
    monkeypatch.setenv("QQEXPORT_HOME",str(tmp_path/"home"))
    class SyntheticApp(ProductApp):
        def _setup_logging(self):
            pass
        def _load_chats(self):
            pass
    root = create_root()
    app = SyntheticApp(root)
    source = SimpleNamespace(account_uin="12345",meta={"synthetic":True})
    infos = [ChatInfo(f"c2c:u_synthetic_{i}","c2c",f"u_synthetic_{i}","12345",f"合成会话 {i}",None,1,i,i)
             for i in range(5000)]
    metas = {info.chat_id:("合成预览",info.last_ts) for info in infos}
    try:
        app._loaded(source,infos,metas,{})
        root.update()
        assert [info.last_ts for info in app.infos]==list(reversed(range(5000)))
        sidebar = app.sidebar
        assert len(sidebar.rows)+len(sidebar.free)<=40
        assert sum(row is not None for row in app.row_widgets)<=40
        top = app.canvas.canvasy(0)
        sidebar.wheel(SimpleNamespace(delta=30))
        root.update()
        assert app.canvas.canvasy(0)==top==0
        sidebar.wheel(SimpleNamespace(delta=-30))
        root.update()
        assert app.canvas.canvasy(0)>0
        for _ in range(40):
            sidebar.wheel(SimpleNamespace(delta=-120))
            root.update()
        assert sidebar.anchor()[0]>10
        assert len(sidebar.rows)+len(sidebar.free)<=40
        assert len(app._row_photos)<=40
        hits = []
        monkeypatch.setattr(app,"_select_chat",lambda index:hits.append(app.infos[index].chat_id))
        index = sidebar.anchor()[0]
        app.row_widgets[index]["labels"][1].event_generate("<Button-1>")
        assert hits==[app.infos[index].chat_id]
        selected = app.infos[index].chat_id
        app.current_chat_id = selected
        anchor_chat = app.infos[index].chat_id
        last = app.infos[-1].chat_id
        metas[last] = ("合成新消息",10000)
        app._loaded(source,infos,metas,{})
        root.update()
        assert app.infos[0].chat_id==last
        assert app.infos[app.selected_idx].chat_id==selected
        assert app.infos[sidebar.anchor()[0]].chat_id==anchor_chat
        sidebar.scroll("moveto",1)
        root.update()
        position = app.canvas.canvasy(0)
        assert app.canvas.yview()[1]==1
        sidebar.wheel(SimpleNamespace(delta=-120))
        root.update()
        assert app.canvas.canvasy(0)==position
        app._loaded(source,infos[:3],metas,{})
        root.update()
        for delta in (120,-120,120):
            sidebar.wheel(SimpleNamespace(delta=delta))
            root.update()
            assert app.canvas.canvasy(0)==0
    finally:
        app.close()
