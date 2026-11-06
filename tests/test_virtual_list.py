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
        import io
        from PIL import Image
        from qqexport.gui_avatars import data_uri
        from qqexport.qq import avatars
        stream = io.BytesIO()
        Image.new("RGB",(64,64),"blue").save(stream,format="PNG")
        portrait = stream.getvalue()
        monkeypatch.setattr(avatars,"fetch_avatar",lambda *args:portrait)
        monkeypatch.setattr(avatars,"fetch_group_avatar",lambda *args:portrait)
        src.meta["synthetic"] = False
        app.gui_avatars.reset(src,app.infos)
        app._start_preview(info)
        wait(lambda:bool(app.avatar_data))
        wait(lambda:any(data_uri(portrait) in row.renderer._avatars for row,_ in app.virtual.rows.values()))
        sidebar_photo = app._row_photos[info.chat_id]
        assert sidebar_photo.tk.call(str(sidebar_photo),"transparency","get",0,0)
        src.meta["synthetic"] = True
        app.gui_avatars.online = False
        assert not root.cget("menu")
        assert app.export_btn.cget("text")=="导出…"
        assert app.next_btn.cget("text")=="下一步"
        completed = []
        monkeypatch.setattr(app,"_export_done",lambda report,output:completed.append(report))
        app._export_selected(app.session,info,{1,2},None,None,{"txt"},tmp_path/"classic-export")
        wait(lambda:bool(completed))
        assert completed[0].messages_total==2
        assert not any(isinstance(child,tk.Toplevel) for child in root.winfo_children())
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
        app._batch_toggle(0)
        from qqexport.queue_ui import BatchDialog,JobDialog
        from qqexport.export.jobs import ExportJob
        from qqexport.export.orchestrator import ExportOptions
        batch = BatchDialog(app)
        assert batch.tree.selection()==("0",)
        batch.destroy()
        job = ExportJob("12345",["c2c:u_a"],ExportOptions(format="txt",out_base=tmp_path/"export"))
        progress = JobDialog(app,app.session,job)
        wait(lambda: progress.tree.item("0","values") and progress.tree.item("0","values")[1]=="成功")
        assert job.tasks[0].messages==101
        progress.close()
        from qqexport.export.archive import create_archive,ArchiveReader
        archive = tmp_path/"synthetic.qqarchive"
        create_archive(repo,["c2c:u_a"],archive)
        reader = ArchiveReader(archive)
        app.mount_archive(reader)
        app._select_chat(0)
        wait(lambda: app.virtual.count==101 and bool(app.virtual.pages))
        assert app.source.meta["archive"] and app._watch_id is None
        from qqexport.settings_ui import SettingsDialog
        from qqexport import config
        settings = SettingsDialog(app)
        settings.density.set("compact")
        settings.limit.set("16")
        settings.save()
        assert config.Config.load().density=="compact"
        assert app.thumbnail_limit==16*1024**2
    finally:
        app.close()


def test_classic_rendering_assets_and_last_line_are_visible(tmp_path):
    import base64,io
    from datetime import datetime
    from PIL import Image
    from qqexport.export.model import MediaRef
    from qqexport.classic_row import media_key
    stream = io.BytesIO()
    Image.new("RGB",(34,34),"blue").save(stream,format="PNG")
    avatar = "data:image/png;base64,"+base64.b64encode(stream.getvalue()).decode()
    picture,second = tmp_path/"synthetic.png",tmp_path/"synthetic2.png"
    Image.new("RGB",(120,65),"blue").save(picture)
    Image.new("RGB",(80,55),"green").save(second)
    message = Message(1,int(datetime(2026,11,6,9).timestamp()),"u_synthetic",None,"合成",True,[
        Segment("image",media=MediaRef("image",source=picture,missing=False)),
        Segment("image",media=MediaRef("image",source=second,missing=False)),
        Segment("text",text="末尾文本用于校验未裁切")],avatar_data=avatar)
    root = create_root()
    root.geometry("800x600")
    view = None
    def request(offset,size):
        view.loaded(offset,[(0,message)] if offset==0 else [])
    def image_load(index,media):
        with Image.open(media.source) as image:
            copied = image.copy()
        root.after_idle(lambda:view.image_ready(index,copied,media_key(media)))
    try:
        view = VirtualMessages(root,request,lambda index:None,image_loader=image_load)
        view.pack(fill="both",expand=True)
        view.reset(1)
        for _ in range(4):
            root.update()
        row = view.rows[0][0]
        assert row.normal.tag_cget("bubble_self","background")=="#95ec69"
        assert row.normal.tag_cget("bubble","background")=="#ffffff"
        assert row.normal.tag_cget("name_self","justify")=="right"
        assert len(row.photos)==2
        last = row.normal.search("末尾文本","1.0")
        box = row.normal.dlineinfo(last)
        assert box and box[1]+box[3]<=row.normal.winfo_height()
        assert row.normal.get("1.0","end").count("2026-11-06")==1
        anchor = view.anchor()
        view.selection(True,{0})
        root.update()
        selection = row.renderer.sel_rows[0]
        assert selection["bg"][0].cget("bg")=="#e6f0ff"
        assert len(row.renderer._sel_photos)==2
        assert not row.normal.winfo_ismapped()
        assert selection["bg"][4].cget("text")=="合成  09:00:00"
        view.selection(False,set())
        root.update()
        assert row.normal.winfo_ismapped() and view.anchor()[0]==anchor[0]
    finally:
        root.destroy()
