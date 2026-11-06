from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import pytest
from PIL import Image
from qqexport.export.model import MediaRef,Message,Segment
from qqexport.thumbnail_cache import ThumbnailCache,image_mime
from qqexport.media_ui import missing_media
from qqexport.export.serde import MediaSnapshot
from qqexport.export import html,orchestrator


def test_content_cache_lru_clear_and_corruption(tmp_path,monkeypatch):
    monkeypatch.setenv("QQEXPORT_HOME",str(tmp_path/"state"))
    source = tmp_path/"synthetic-noextension"
    Image.new("RGB",(800,600),"red").save(source,format="PNG")
    cache = ThumbnailCache()
    media = MediaRef("image",source=source,missing=False)
    assert cache.load(media).size==(240,180)
    files = list(cache.root.glob("*.png"))
    assert len(files)==1 and str(source) not in files[0].name
    files[0].write_bytes(b"corrupt synthetic thumbnail")
    assert cache.load(media).size==(240,180)
    cache.limit = 1
    cache.load(replace(media,data_uri=None))
    with cache.lease():
        cache._trim()
    assert not list(cache.root.glob("*.png"))
    cache.limit = 100000
    cache.load(media)
    cache.clear()
    assert not list(cache.root.glob("*.png"))


def test_dedup_across_extensions_and_streamed_inline(tmp_path):
    first,second = tmp_path/"image-one",tmp_path/"image-two.jpg"
    Image.new("RGB",(20,20),"blue").save(first,format="PNG")
    second.write_bytes(first.read_bytes())
    snapshot = MediaSnapshot(tmp_path/"snapshot")
    one = snapshot.media(MediaRef("image",source=first,missing=False))
    two = snapshot.media(MediaRef("image",source=second,missing=False))
    assert one.rel_path==two.rel_path
    message = Message(msg_id=1,ts=1,sender_uid="u_synthetic",sender_uin=None,sender_name="Synthetic",is_self=False,
        segments=[Segment("image",media=MediaRef("image",source=first,missing=False))])
    ready = orchestrator._inline_images([message])
    assert ready[0].segments[0].media.data_uri is None
    assert "data:image/png;base64," in html._msg_html(ready[0])
    assert image_mime(first.read_bytes()[:16])=="image/png"


def test_missing_records_have_position_id_time_kind_without_body(tmp_path):
    missing = MediaRef("voice",missing=True)
    message = Message(msg_id=42,ts=123,sender_uid="u_synthetic",sender_uin=None,sender_name="Synthetic",is_self=False,
                      segments=[Segment("voice",media=missing)])
    repo = SimpleNamespace(iter_messages=lambda chat:iter([message]))
    assert list(missing_media(repo,"c2c:synthetic"))==[(0,42,123,"voice")]
