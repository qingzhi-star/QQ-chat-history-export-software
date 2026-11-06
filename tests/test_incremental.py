from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import threading

import pytest

from qqexport.export import orchestrator
from qqexport.export.incremental import IncrementalConflict,read_manifest,target_lock
from qqexport.export.serde import records,from_data,SnapshotError,message_data,logical_hash
from qqexport.export.cancel import ExportCancelled
from tests.test_repository import source
from tests.helpers import synth_db as sd


def add(src,*,ts=33,msg_id=102,text="new synthetic"):
    row = sd.text_row(ts=ts,msg_id=msg_id,text=text,uid="u_a")
    con = sqlite3.connect(src.msg_db)
    keys = list(row)
    con.execute('INSERT INTO c2c_msg_table ('+','.join('"'+key+'"' for key in keys)+') VALUES ('+
                ','.join('?' for _ in keys)+')',tuple(row[k] for k in keys))
    con.commit()
    con.close()


def test_incremental_appends_without_duplicates_and_is_idempotent(tmp_path):
    src = source(tmp_path)
    opts = orchestrator.ExportOptions(formats={"txt","html"},out_base=tmp_path/"out",incremental=True)
    first = orchestrator.export_chats(src,["c2c:u_a"],opts)
    folder = Path(first.exported_dirs[0])
    old_text = (folder/"chat.txt").read_bytes()
    assert first.messages_added==101
    add(src)
    second = orchestrator.export_chats(src,["c2c:u_a"],opts)
    assert second.messages_added==1 and second.messages_total==102 and second.consistent
    text = (folder/"chat.txt").read_bytes()
    assert text.startswith(old_text) and text.count(b"\xef\xbb\xbf")==1
    assert [m.msg_id for m in records(folder/"messages.jsonl")] == list(range(1,103))
    html = (folder/"chat.html").read_text(encoding="utf-8")
    assert html.count("new synthetic")==1 and "102 条" in html
    assert str(tmp_path) not in (folder/"messages.jsonl").read_text(encoding="utf-8")
    stamps = {p.name:p.stat().st_mtime_ns for p in folder.iterdir() if p.is_file()}
    third = orchestrator.export_chats(src,["c2c:u_a"],opts)
    assert third.messages_added==0
    assert stamps == {p.name:p.stat().st_mtime_ns for p in folder.iterdir() if p.is_file()}
    assert not list(folder.parent.glob(".inc-lock-*"))


@pytest.mark.parametrize("change",["truncate","source_boundary","backfill","manifest_count"])
def test_incremental_conflicts_never_overwrite_existing(tmp_path,change):
    src = source(tmp_path)
    opts = orchestrator.ExportOptions(formats={"txt","html"},out_base=tmp_path/"out",incremental=True)
    report = orchestrator.export_chats(src,["c2c:u_a"],opts)
    folder = Path(report.exported_dirs[0])
    manifest = (folder/"manifest.json").read_bytes()
    if change=="truncate":
        (folder/"chat.txt").write_bytes(b"truncated")
    elif change=="source_boundary":
        con = sqlite3.connect(src.msg_db)
        con.execute('DELETE FROM c2c_msg_table WHERE "40001"=101')
        con.commit()
        con.close()
    elif change=="backfill":
        add(src,ts=1,msg_id=103,text="backfill synthetic")
    else:
        data = json.loads(manifest)
        data["stats"]["total"] -= 1
        data["stats"]["ok"] -= 1
        (folder/"manifest.json").write_text(json.dumps(data),encoding="utf-8")
        add(src)
    manifest = (folder/"manifest.json").read_bytes()
    old = (folder/"chat.txt").read_bytes()
    with pytest.raises(IncrementalConflict):
        orchestrator.export_chats(src,["c2c:u_a"],opts)
    assert (folder/"manifest.json").read_bytes()==manifest
    assert (folder/"chat.txt").read_bytes()==old
    assert not list(folder.parent.glob(".tmp-*"))


def test_legacy_requires_explicit_rebuild_and_keeps_previous_manifest(tmp_path):
    src = source(tmp_path)
    opts = orchestrator.ExportOptions(format="txt",out_base=tmp_path/"out")
    report = orchestrator.export_chats(src,["c2c:u_a"],opts)
    folder = Path(report.exported_dirs[0])
    inc = replace(opts,incremental=True)
    with pytest.raises(IncrementalConflict,match="旧格式"):
        orchestrator.export_chats(src,["c2c:u_a"],inc)
    orchestrator.export_chats(src,["c2c:u_a"],replace(inc,rebuild=True))
    previous = (folder/"manifest.json").read_bytes()
    orchestrator.export_chats(src,["c2c:u_a"],replace(inc,rebuild=True))
    assert (folder/"previous-manifest.json").read_bytes()==previous


def test_cancel_before_publish_keeps_entire_old_snapshot(tmp_path,monkeypatch):
    src = source(tmp_path)
    opts = orchestrator.ExportOptions(formats={"txt","html"},out_base=tmp_path/"out",incremental=True)
    report = orchestrator.export_chats(src,["c2c:u_a"],opts)
    folder = Path(report.exported_dirs[0])
    old = (folder/"manifest.json").read_bytes()
    add(src)
    event = threading.Event()
    from qqexport.export import incremental
    real = incremental.html.append_html
    def interrupted(*args,**kwargs):
        real(*args,**kwargs)
        event.set()
    monkeypatch.setattr(incremental.html,"append_html",interrupted)
    with pytest.raises(ExportCancelled):
        orchestrator.export_chats(src,["c2c:u_a"],replace(opts,cancel=event))
    assert (folder/"manifest.json").read_bytes()==old
    assert not list(folder.parent.glob(".tmp-*"))


def test_snapshot_path_escape_and_active_lock_rejected(tmp_path):
    from qqexport.export.model import Message,Segment,MediaRef
    msg = Message(1,1,None,None,"synthetic",False,[Segment("image",media=MediaRef("image",rel_path="../outside.png"))])
    with pytest.raises(SnapshotError):
        from_data(message_data(msg),tmp_path)
    with target_lock(tmp_path,"c2c:u_a","synthetic"):
        with pytest.raises(IncrementalConflict,match="已有"):
            with target_lock(tmp_path,"c2c:u_a","synthetic"):
                pytest.fail("lock should be exclusive")


def test_incremental_pdf_rebuild_uses_updated_complete_html(tmp_path,monkeypatch):
    from qqexport.export import pdf
    captures = []
    def render(html_path,out,**kwargs):
        captures.append(html_path.read_text(encoding="utf-8"))
        out.write_bytes(b"%PDF synthetic "+str(len(captures)).encode("ascii"))
        return pdf.PdfResult(out)
    monkeypatch.setattr(pdf,"write_pdf",render)
    src = source(tmp_path)
    opts = orchestrator.ExportOptions(formats={"pdf"},out_base=tmp_path/"out",incremental=True)
    first = orchestrator.export_chats(src,["c2c:u_a"],opts)
    folder = Path(first.exported_dirs[0])
    assert (folder/"_state/chat.html").is_file() and not (folder/"chat.html").exists()
    add(src)
    second = orchestrator.export_chats(src,["c2c:u_a"],opts)
    assert second.messages_added==1 and len(captures)==2
    assert captures[1].count("new synthetic")==1 and "102 条" in captures[1]
