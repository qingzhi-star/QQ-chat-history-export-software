import base64,io
from pathlib import Path
from types import SimpleNamespace
from PIL import Image
from qqexport.export.model import MediaRef,Segment
from qqexport.viewer_images import prepare,clear
from qqexport.gui_avatars import rounded,placeholder
from qqexport.export import txt,html


def test_rounded_avatar_and_placeholder_have_transparent_corners():
    stream = io.BytesIO()
    Image.new("RGB",(80,50),"red").save(stream,format="PNG")
    for size in (34,44):
        image = rounded(stream.getvalue(),size)
        assert image.size==(size,size)
        assert image.getpixel((0,0))[3]==0
        assert image.getpixel((size//2,size//2))[3]==255
        with Image.open(io.BytesIO(placeholder("合成",size))) as fallback:
            assert fallback.getpixel((0,0))[3]==0


def test_default_viewer_copy_preserves_original_and_archive_independence(tmp_path,monkeypatch):
    monkeypatch.setenv("QQEXPORT_HOME",str(tmp_path/"state"))
    source = tmp_path/"synthetic-original.bin"
    Image.new("RGB",(30,20),"blue").save(source,format="PNG")
    original = source.read_bytes()
    target = prepare(MediaRef("image",source=source,missing=False))
    assert target!=source and target.suffix==".png" and target.read_bytes()==original
    source.unlink()
    assert target.read_bytes()==original
    uri = "data:image/png;base64,"+base64.b64encode(original).decode()
    assert prepare(MediaRef("image",data_uri=uri,missing=False))==target
    target.write_bytes(b"edited independent image copy")
    assert source.exists() is False
    clear()
    assert not target.exists()


def test_images_open_through_system_association_without_internal_viewer(tmp_path,monkeypatch):
    from qqexport import archive_ui
    monkeypatch.setenv("QQEXPORT_HOME",str(tmp_path/"state"))
    source = tmp_path/"synthetic.png"
    Image.new("RGB",(20,20),"red").save(source)
    opened = []
    monkeypatch.setattr(archive_ui.os,"startfile",opened.append)
    app = SimpleNamespace(session=object(),root=None)
    app._submit = lambda session,work,done:done(work(None))
    archive_ui.open_attachment(app,MediaRef("image",source=source,missing=False))
    assert len(opened)==1 and opened[0]!=source
    assert opened[0].read_bytes()==source.read_bytes()


def test_files_keep_prefix_and_filename_across_projections(tmp_path):
    segment = Segment("file",media=MediaRef("file",filename="合成报告.pdf",rel_path="media/synthetic.bin",missing=False))
    assert txt.segment_text(segment)=="[文件] 合成报告.pdf"
    assert "[文件] 合成报告.pdf" in html._seg_html(segment)
    assert "[文件] 合成报告.pdf" in html._seg_html(segment,link_files=False)
