import json
import threading
from dataclasses import replace

import pytest

from qqexport.export import orchestrator
from qqexport.export.cancel import ExportCancelled,check_cancel
from qqexport.export.jobs import ExportJob,JobError
from qqexport.export.session import ChatInfo
from tests.test_orchestrator import make_source


def test_queue_isolates_failure_skips_empty_and_retries_only_failed(tmp_path,monkeypatch):
    src = make_source(tmp_path)
    good = src.chats()[0]
    bad = replace(good,chat_id="c2c:u_bad",peer="u_bad",title="合成失败")
    empty = replace(good,chat_id="c2c:u_empty",peer="u_empty",title="合成空会话")
    monkeypatch.setattr(src,"chats",lambda:[bad,good,empty])
    original = src.iter_messages
    failing = [True]
    def rows(chat):
        if chat==bad.chat_id and failing[0]:
            raise OSError("synthetic-private-message")
        return iter(()) if chat==empty.chat_id else original(good.chat_id)
    monkeypatch.setattr(src,"iter_messages",rows)
    state = tmp_path/"queue.json"
    job = ExportJob(src.account_uin,[bad.chat_id,good.chat_id,empty.chat_id],
        orchestrator.ExportOptions(format="txt",out_base=tmp_path/"out"),checkpoint=state,write_manifest=True)
    events = []
    job.run(src,events.append)
    assert [t.status for t in job.tasks]==["failed","done","skipped"]
    assert len(events)>=4
    assert "synthetic-private-message" not in state.read_text(encoding="utf-8")
    good_file = job.root/job.tasks[1].files[0]["path"]
    stamp = good_file.stat().st_mtime_ns
    failing[0] = False
    job.retry_failed()
    job.run(src)
    assert [t.status for t in job.tasks]==["done","done","skipped"]
    assert job.tasks[1].attempts==1 and good_file.stat().st_mtime_ns==stamp
    assert (job.root/"manifest.json").is_file()
    resumed = ExportJob.load(state,src.account_uin)
    resumed.run(src)
    assert good_file.stat().st_mtime_ns==stamp
    raw = state.read_bytes()
    good_file.write_bytes(b"tampered")
    with pytest.raises(JobError,match="修改"):
        ExportJob.load(state,src.account_uin)
    assert state.read_bytes()==raw


def test_cancel_mid_write_preserves_old_output_and_cleans_temp(tmp_path,monkeypatch):
    src = make_source(tmp_path)
    opts = orchestrator.ExportOptions(format="txt",out_base=tmp_path/"out")
    old = orchestrator.export_chats(src,["c2c:u_a"],opts)
    path = next(__import__("pathlib").Path(old.exported_dirs[0]).glob("chat.txt"))
    old_bytes = path.read_bytes()
    def interrupted(chat,out,options):
        out.write_bytes(b"partial synthetic")
        options.cancel.set()
        check_cancel(options.cancel)
    monkeypatch.setattr(orchestrator.txt_mod,"write_txt",interrupted)
    job = ExportJob(src.account_uin,["c2c:u_a"],opts,checkpoint=tmp_path/"job.json")
    job.run(src)
    assert job.tasks[0].status=="cancelled"
    assert path.read_bytes()==old_bytes
    assert not list(job.root.glob(".tmp-*"))
    restored = ExportJob.load(tmp_path/"job.json",src.account_uin)
    assert restored.tasks[0].status=="pending"


def test_checkpoint_wrong_account_and_corrupt_record_never_overwritten(tmp_path):
    src = make_source(tmp_path)
    job = ExportJob(src.account_uin,["c2c:u_a"],orchestrator.ExportOptions(format="txt",out_base=tmp_path/"out"),
                    checkpoint=tmp_path/"job.json")
    job.run(src)
    path = tmp_path/"job.json"
    original = path.read_bytes()
    with pytest.raises(JobError,match="账号"):
        ExportJob.load(path,"12345")
    assert path.read_bytes()==original
    path.write_bytes(b"{bad")
    with pytest.raises(JobError,match="损坏"):
        ExportJob.load(path,src.account_uin)
    assert path.read_bytes()==b"{bad"


def test_pre_cancel_does_not_create_output(tmp_path):
    src = make_source(tmp_path)
    event = threading.Event()
    event.set()
    with pytest.raises(ExportCancelled):
        orchestrator.export_chats(src,["c2c:u_a"],orchestrator.ExportOptions(out_base=tmp_path/"out",cancel=event))
    assert not (tmp_path/"out").exists()


def test_export_media_is_independent_copy(tmp_path):
    from qqexport.export.media_copy import copy_media
    from qqexport.export.model import MediaRef
    path = tmp_path/"original.txt"
    path.write_bytes(b"original")
    copied = copy_media([MediaRef("file",source=path,missing=False)],tmp_path/"media")
    (tmp_path/"media"/copied.rel_by_source[path]).write_bytes(b"modified backup")
    assert path.read_bytes()==b"original"


def test_batch_requires_explicit_all_confirmation_before_data(monkeypatch):
    from qqexport import cli
    monkeypatch.setattr(cli,"_open_source",lambda args:pytest.fail("must not open QQ"))
    with pytest.raises(SystemExit) as exc:
        cli.main(["batch","--all"])
    assert exc.value.code==2


def test_queue_cli_checkpoint_resumes_completed_without_overwrite(tmp_path,monkeypatch,capsys):
    from qqexport import cli
    src = make_source(tmp_path)
    monkeypatch.setattr(cli,"_open_source",lambda args:src)
    path = tmp_path/"queue.json"
    assert cli.main(["batch","--chat","c2c:u_a","--format","txt","--out",str(tmp_path/"out"),
                     "--checkpoint",str(path)])==0
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["tasks"][0]["attempts"]==1
    assert cli.main(["batch","--resume",str(path)])==0
    assert json.loads(path.read_text(encoding="utf-8"))["tasks"][0]["attempts"]==1


def test_queue_partial_format_failure_keeps_old_directory(tmp_path,monkeypatch):
    from qqexport.export.pdf import PdfResult
    src = make_source(tmp_path)
    opts = orchestrator.ExportOptions(format="txt",out_base=tmp_path/"out")
    old = orchestrator.export_chats(src,["c2c:u_a"],opts)
    from pathlib import Path
    folder = Path(old.exported_dirs[0])
    (folder/"chat.pdf").write_bytes(b"old PDF")
    old_txt = (folder/"chat.txt").read_bytes()
    monkeypatch.setattr(orchestrator.pdf_mod,"write_pdf",lambda *a,**kw:PdfResult(None,"synthetic PDF failure"))
    job = ExportJob(src.account_uin,["c2c:u_a"],replace(opts,formats={"txt","pdf"}))
    job.run(src)
    assert job.tasks[0].status=="failed"
    assert (folder/"chat.pdf").read_bytes()==b"old PDF"
    assert (folder/"chat.txt").read_bytes()==old_txt


def test_manifest_cannot_write_inside_qq_source_even_with_no_tasks(tmp_path):
    from types import SimpleNamespace
    src = make_source(tmp_path)
    protected = tmp_path/"QQ source"
    src.meta.update(synthetic=False,account=SimpleNamespace(data_dir=protected))
    job = ExportJob(src.account_uin,[],orchestrator.ExportOptions(out_base=protected),write_manifest=True)
    with pytest.raises(JobError,match="QQ 源"):
        job.run(src)
    assert not protected.exists()
