"""绮录品牌资源、账号卡片、底部控件和居中导出设置的真实 Tk 回归。"""
import time
import tkinter as tk
from PIL import Image
from qqexport.branding import ASSETS
from qqexport.core.discover import QQAccount
from qqexport.gui import FormatDialog
from qqexport.product_gui import ProductApp
from qqexport.qq.contacts import Friend
from qqexport.repository import MessageRepository
from qqexport.tasks import DataSession
from qqexport.tk_runtime import create_root
from tests.test_repository import source


def test_brand_assets_have_native_windows_sizes():
    with Image.open(ASSETS/"qilu.png") as image:
        assert image.mode=="RGBA" and image.size==(512,512)
        assert image.getpixel((0,0))[3]==0
    with Image.open(ASSETS/"qilu.ico") as icon:
        assert {(16,16),(32,32),(48,48),(256,256)}<=icon.ico.sizes()


def test_profile_footer_and_centered_export_dialog(tmp_path,monkeypatch):
    monkeypatch.setenv("QQEXPORT_HOME",str(tmp_path/"home"))
    src = source(tmp_path,count=5)
    src.meta["account"] = QQAccount("12345",tmp_path,tmp_path/"nt_db",tmp_path/"nt_data")
    src.contacts.friends_by_uin["12345"] = Friend(uin="12345",nick="合成用户名")
    repo = MessageRepository(src)
    class SyntheticApp(ProductApp):
        def _setup_logging(self):
            pass
        def _load_chats(self):
            self.session = DataSession(src,repo)
            infos,metas = repo.sidebar()
            self._loaded(src,infos,metas,{})
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
    try:
        root.update()
        assert root.title()=="绮录" and root._brand_icon
        assert app.accounts_panel.name.cget("text")=="合成用户名"
        assert app.accounts_panel.number.cget("text")=="QQ 12345"
        assert app.account_button.winfo_rooty()<app.canvas.winfo_rooty()
        assert not app.count_label.winfo_ismapped()
        app._select_chat(0)
        wait(lambda:app.virtual.count==5 and not app._busy)
        app._open_export()
        app._toggle_all()
        root.update()
        assert app.all_btn.winfo_rooty()>app.virtual.winfo_rooty()+app.virtual.winfo_height()
        assert abs(app.next_btn.winfo_rooty()-app.date_btn.winfo_rooty())<3
        app._next_step()
        wait(lambda:any(isinstance(child,FormatDialog) for child in root.winfo_children()))
        dialog = next(child for child in root.winfo_children() if isinstance(child,FormatDialog))
        root.update()
        assert abs((dialog.winfo_x()+dialog.winfo_width()/2)-(root.winfo_x()+root.winfo_width()/2))<5
        assert abs((dialog.winfo_y()+dialog.winfo_height()/2)-(root.winfo_y()+root.winfo_height()/2))<5
        assert dialog.title()=="绮录 · 导出设置"
        assert set(dialog.format_cards)=={"txt","html","pdf"}
        dialog.destroy()
        app._cancel_export()
        assert not app.export_mode and not app.selected_msgs
    finally:
        app.close()


def test_format_dialog_keeps_allowed_formats_and_callback(tmp_path):
    root = create_root()
    selected = []
    try:
        dialog = FormatDialog(root,str(tmp_path),allowed_formats={"txt","html"},
                              default_formats={"txt","pdf"},on_ok=lambda formats,out:selected.append((formats,out)))
        root.update()
        assert set(dialog.format_cards)=={"txt","html"}
        assert not dialog.vars["pdf"].get()
        card = dialog.format_cards["html"]
        card.event_generate("<Button-1>",x=card.winfo_width()-10,y=card.winfo_height()//2)
        assert dialog.vars["html"].get()
        assert card.cget("bg")=="#f0eafa"
        card.description.event_generate("<Button-1>")
        assert not dialog.vars["html"].get()
        card.check.event_generate("<Button-1>")
        assert dialog.vars["html"].get()
        card.toggle()
        assert not dialog.vars["html"].get()
        dialog._ok()
        assert selected==[({"txt"},tmp_path)]
    finally:
        root.destroy()
