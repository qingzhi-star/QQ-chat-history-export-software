from dataclasses import replace
import json
from qqexport.export.model import MediaRef,Segment
from qqexport.export import html,txt,orchestrator
from qqexport.qq import messages
from tests.helpers import synth_proto as sp
from tests.test_messages import row,ctx
from tests.test_orchestrator import make_source


def test_resolver_keeps_duration_dimensions_and_size(tmp_path):
    context = ctx(resolve_media=lambda kind,*args:MediaRef(kind,source=tmp_path/"synthetic",missing=False))
    parsed = messages.parse_message(row(sp.message(sp.voice_element(12500),sp.image_element(md5=b"a"*16,width=800,height=600,size=4096))),context)
    assert parsed.segments[0].media.duration_ms==12500
    assert "12.5 秒" in txt.segment_text(parsed.segments[0])
    image = parsed.segments[1].media
    assert (image.width,image.height,image.size)==(800,600,4096)


def test_cached_forward_and_cards_project_without_execution():
    payload = json.dumps({"messages":[{"sender":"Synthetic","text":"<script>cached full text</script>"}]})
    parsed = messages.parse_message(row(sp.message(sp.forward_nt_element(payload)),outer=11),ctx())
    segment = parsed.segments[0]
    assert "cached full text" in txt.segment_text(segment)
    rendered = html._seg_html(segment)
    assert "&lt;script&gt;" in rendered and "<script>" not in rendered
    uncached = replace(segment,forward_previews=None)
    assert "未展开" in txt.segment_text(uncached)
    card = Segment("ark",forward_meta={"meta":{"detail":{"title":"Synthetic title <script>"}}})
    assert "Synthetic title" in txt.segment_text(card)
    assert "&lt;script&gt;" in html._seg_html(card)


def test_voice_raw_export_independent_copy_and_pdf_label(tmp_path):
    source = make_source(tmp_path)
    audio = tmp_path/"synthetic.silk"
    audio.write_bytes(b"#!SILK_V3 synthetic raw audio")
    original = source.iter_messages
    def with_voice(chat):
        for message in original(chat):
            yield replace(message,segments=[Segment("voice",media=MediaRef("voice",source=audio,missing=False,duration_ms=12000,filename="synthetic.silk"))])
    source.iter_messages = with_voice
    report = orchestrator.export_chats(source,["c2c:u_a"],orchestrator.ExportOptions(formats={"txt","html"},out_base=tmp_path/"out"))
    output = next((tmp_path/"out"/orchestrator.ROOT_FOLDER).iterdir())
    page = (output/"chat.html").read_text(encoding="utf-8")
    assert "12 秒" in page and "下载原始音频" in page
    copied = list((output/"media").iterdir())
    assert len(copied)==1 and copied[0].read_bytes()==audio.read_bytes()
    copied[0].write_bytes(b"modified exported synthetic audio")
    assert audio.read_bytes().startswith(b"#!SILK_V3")
    segment = next(with_voice("c2c:u_a")).segments[0]
    assert "12 秒" in html._seg_html(segment,link_files=False)
    assert "href=" not in html._seg_html(segment,link_files=False)
    assert report.chats_exported==1
