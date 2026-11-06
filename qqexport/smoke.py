"""打包冒烟入口：构建真实主界面，但不发现账号、不读取 QQ、不联网。"""
from __future__ import annotations

import json
from pathlib import Path


def verify_offline():
    """冻结环境也验证加密、Pillow、归档、搜索和导出依赖确实可用。"""
    import tempfile
    from types import SimpleNamespace
    from PIL import Image
    from qqexport.export.model import Message,Segment,MediaRef
    from qqexport.export.session import ChatInfo
    from qqexport.export.archive import create_archive,ArchiveReader,ArchiveRepository
    from qqexport.export.orchestrator import export_chats,ExportOptions
    from qqexport.search import SearchIndex,Query
    from qqexport.thumbnail_cache import ThumbnailCache
    with tempfile.TemporaryDirectory(prefix="qqexport-smoke-offline-") as temp:
        root = Path(temp)
        image = root/"synthetic.png"
        Image.new("RGB",(320,240),"blue").save(image)
        messages = [Message(1,100,"u_synthetic",None,"Synthetic",False,
            [Segment("text",text="synthetic search"),Segment("image",media=MediaRef("image",source=image,missing=False))])]
        info = ChatInfo("c2c:u_synthetic","c2c","u_synthetic","u_synthetic","合成冒烟",None,1,100,100)
        source = SimpleNamespace(meta={"synthetic":True},account_uin="synthetic",msg_db=image,
                                 chats=lambda:[info],iter_messages=lambda chat:iter(messages))
        archive = root/"synthetic.qqarchive"
        create_archive(source,[info.chat_id],archive,password="synthetic-smoke-passphrase")
        reader,index = ArchiveReader(archive,password="synthetic-smoke-passphrase"),SearchIndex()
        try:
            offline = reader.source()
            index.build(ArchiveRepository(offline))
            assert index.find(Query("synthetic"))
            media = next(offline.iter_messages(info.chat_id)).segments[1].media
            assert ThumbnailCache(limit=0).load(media).size==(240,180)
            report = export_chats(offline,[info.chat_id],ExportOptions(formats={"txt","html"},out_base=root/"out"))
            assert report.chats_exported==1
        finally:
            index.close()
            reader.close()


def run_smoke(report: Path) -> int:
    root = app = None
    result = {"ok": False}
    try:
        import tkinter as tk
        from qqexport import __version__
        from qqexport.product_gui import ProductApp as App

        class SmokeApp(App):
            def _load_chats(self):
                self.count_label.configure(text="合成启动检查")

        from qqexport.tk_runtime import create_root
        root = create_root()
        root.withdraw()
        app = SmokeApp(root)
        root.update()
        assert app.export_btn.winfo_exists() and app.text.winfo_exists()
        verify_offline()
        result.update(ok=True, version=__version__, tcl=root.tk.call("info", "patchlevel"),
                      account_discovery=False, window_title=root.title(),offline_pipeline=True)
        return 0
    except Exception as exc:
        result["error"] = type(exc).__name__
        return 1
    finally:
        if app is not None:
            app.close()
        elif root is not None:
            root.destroy()
        report.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
