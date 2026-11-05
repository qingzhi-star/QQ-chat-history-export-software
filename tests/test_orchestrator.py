"""导出编排测试：单格式、固定大目录、名称-账号、无多余文件。"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import shutil
import pytest

from qqexport.export import orchestrator
from qqexport.export.session import ExportSource
from qqexport.qq import contacts, media
from tests.helpers import synth_db as sd

NOW = datetime(2026, 10, 30, 12, 0, 0)


def test_commit_retries_transient_windows_lock(tmp_path, monkeypatch):
    source, target = tmp_path / "source", tmp_path / "target"
    source.mkdir()
    (source / "complete.txt").write_text("complete", encoding="utf-8")
    original = Path.rename
    calls = []

    def rename(path, destination):
        calls.append(path)
        if len(calls) == 1:
            exc = PermissionError("synthetic sharing violation")
            exc.winerror = 32
            raise exc
        return original(path, destination)

    monkeypatch.setattr(Path, "rename", rename)
    monkeypatch.setattr(orchestrator.time, "sleep", lambda seconds: None)
    orchestrator._commit_dir(source, target)
    assert (target / "complete.txt").read_text(encoding="utf-8") == "complete"
    assert len(calls) == 2


def test_commit_permanent_failure_remains_atomic(tmp_path, monkeypatch):
    source, target = tmp_path / "source", tmp_path / "target"
    source.mkdir()
    target.mkdir()
    (target / "old.txt").write_text("old", encoding="utf-8")
    original = Path.rename

    def rename(path, destination):
        if path == source:
            exc = PermissionError("synthetic permanent lock")
            exc.winerror = 5
            raise exc
        return original(path, destination)

    monkeypatch.setattr(Path, "rename", rename)
    monkeypatch.setattr(orchestrator.time, "sleep", lambda seconds: None)
    with pytest.raises(PermissionError):
        orchestrator._commit_dir(source, target)
    assert (target / "old.txt").read_text(encoding="utf-8") == "old"


def make_source(tmp_path):
    md5 = "a" * 32
    media_dir = tmp_path / "nt_data"
    (media_dir / "Pic" / "2024-01" / "Ori").mkdir(parents=True)
    (media_dir / "Pic" / "2024-01" / "Ori" / f"{md5}.jpg").write_bytes(b"img")
    profile = sd.create_companion_db(
        tmp_path / "profile.db", kind="profile",
        rows={"profile_info_v6": [
            {"1000": "u_a", "1002": 123456789, "20002": "昵称A", "20009": None}]})
    group = sd.create_companion_db(
        tmp_path / "group.db", kind="group",
        rows={"group_list": [{"60001": 111, "60007": "群 名"}]})
    c = contacts.Contacts.load(profile, group, account_uin="123456789")
    import tests.helpers.synth_proto as sp
    row = sd.text_row(ts=100, text="你好", uid="u_a")
    row2 = sd.text_row(ts=200, text="看图", uid="u_a", msg_id=2)
    row2["40800"] = sp.message(sp.image_element(md5=bytes.fromhex(md5),
                                                filename=f"{md5}.jpg"))
    db = sd.create_msg_db(tmp_path / "nt_msg.db", c2c=[row, row2])
    return ExportSource.from_parts(db, c, media.build_index(media_dir), "123456789")


def test_folder_name_keeps_spaces_and_account():
    info = orchestrator.ChatInfo if hasattr(orchestrator, "ChatInfo") else None
    from qqexport.export.session import ChatInfo
    name = orchestrator.folder_name(ChatInfo(
        "group:1", "group", "111", "111", "群 名", "群号 111", 3, 0, 1))
    assert name == "群 名-111"


def test_txt_exports_only_chat_txt(tmp_path):
    src = make_source(tmp_path)
    out = tmp_path / "out"
    rep = orchestrator.export_chats(
        src, ["c2c:u_a"], orchestrator.ExportOptions(
            format="txt", out_base=out, now=NOW))
    root = out / orchestrator.ROOT_FOLDER
    dirs = [p for p in root.iterdir() if p.is_dir()]
    assert len(dirs) == 1
    assert dirs[0].name == "昵称A-987654321"
    assert sorted(p.name for p in dirs[0].iterdir()) == ["chat.txt"]
    assert rep.messages_total == 2 and rep.consistent
    assert "media" not in {p.name for p in dirs[0].iterdir()}


def test_html_exports_chat_and_media_only(tmp_path):
    src = make_source(tmp_path)
    out = tmp_path / "out"
    rep = orchestrator.export_chats(
        src, ["c2c:u_a"], orchestrator.ExportOptions(
            format="html", out_base=out, now=NOW))
    folder = out / orchestrator.ROOT_FOLDER / "昵称A-987654321"
    assert (folder / "chat.html").is_file()
    assert {p.name for p in folder.iterdir()} == {"chat.html"}
    assert rep.media_found == 1
    text = (folder / "chat.html").read_text(encoding="utf-8")
    assert "data:image" in text and "http" not in text


def test_pdf_exports_only_pdf(tmp_path, monkeypatch):
    src = make_source(tmp_path)
    out = tmp_path / "out"

    def fake_pdf(html_path, out_pdf, **kw):
        out_pdf.write_bytes(b"%PDF-1.4 fake")
        from qqexport.export.pdf import PdfResult
        return PdfResult(out_pdf)

    monkeypatch.setattr(orchestrator.pdf_mod, "write_pdf", fake_pdf)
    rep = orchestrator.export_chats(
        src, ["c2c:u_a"], orchestrator.ExportOptions(
            format="pdf", out_base=out, now=NOW))
    folder = out / orchestrator.ROOT_FOLDER / "昵称A-987654321"
    assert {p.name for p in folder.iterdir()} == {"chat.pdf"}
    assert rep.chats_exported == 1


def test_time_range_and_message_filters(tmp_path):
    src = make_source(tmp_path)
    out = tmp_path / "out"
    rep = orchestrator.export_chats(
        src, ["c2c:u_a"], orchestrator.ExportOptions(
            format="txt", out_base=out, now=NOW, start_ts=150))
    folder = out / orchestrator.ROOT_FOLDER / "昵称A-987654321"
    lines = (folder / "chat.txt").read_text(encoding="utf-8-sig").splitlines()
    assert len(lines) == 1 and "[图片]" in lines[0]
    assert rep.messages_total == 1

    out2 = tmp_path / "out2"
    orchestrator.export_chats(
        src, ["c2c:u_a"], orchestrator.ExportOptions(
            format="txt", out_base=out2, now=NOW, message_ids={1}))
    folder2 = out2 / orchestrator.ROOT_FOLDER / "昵称A-987654321"
    assert (folder2 / "chat.txt").read_text(encoding="utf-8-sig").count("\n") == 1


def test_failed_export_keeps_existing_output(tmp_path, monkeypatch):
    src = make_source(tmp_path)
    out = tmp_path / "out"
    opts = orchestrator.ExportOptions(format="txt", out_base=out, now=NOW)
    orchestrator.export_chats(src, ["c2c:u_a"], opts)
    folder = out / orchestrator.ROOT_FOLDER / "昵称A-987654321"
    marker = folder / "keep-me.txt"
    marker.write_text("old", encoding="utf-8")

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(orchestrator.txt_mod, "write_txt", boom)
    rep = orchestrator.export_chats(src, ["c2c:u_a"], opts)
    assert marker.is_file()
    assert (folder / "chat.txt").is_file()
    assert rep.chats_exported == 0
    root = out / orchestrator.ROOT_FOLDER
    assert not [p for p in root.iterdir() if p.name.startswith(".tmp-")]
    assert not [p for p in root.iterdir() if ".old-" in p.name]


def test_pdf_failure_does_not_publish_empty_folder(tmp_path, monkeypatch):
    src = make_source(tmp_path)
    out = tmp_path / "out"

    def fail_pdf(html_path, out_pdf, **kw):
        from qqexport.export.pdf import PdfResult
        return PdfResult(None, error="pdf boom")

    monkeypatch.setattr(orchestrator.pdf_mod, "write_pdf", fail_pdf)
    rep = orchestrator.export_chats(
        src, ["c2c:u_a"], orchestrator.ExportOptions(
            format="pdf", out_base=out, now=NOW))
    root = out / orchestrator.ROOT_FOLDER
    assert not (root / "昵称A-987654321").exists()
    assert rep.chats_exported == 0
    assert not [p for p in root.iterdir() if p.name.startswith(".tmp-")]


def test_successful_reexport_replaces_old_folder(tmp_path):
    src = make_source(tmp_path)
    out = tmp_path / "out"
    opts = orchestrator.ExportOptions(format="txt", out_base=out, now=NOW)
    orchestrator.export_chats(src, ["c2c:u_a"], opts)
    folder = out / orchestrator.ROOT_FOLDER / "昵称A-987654321"
    (folder / "stale.txt").write_text("old", encoding="utf-8")
    orchestrator.export_chats(src, ["c2c:u_a"], opts)
    assert not (folder / "stale.txt").exists()
    assert (folder / "chat.txt").is_file()
    root = out / orchestrator.ROOT_FOLDER
    assert not [p for p in root.iterdir() if ".old-" in p.name]


def test_all_formats_share_same_selected_snapshot(tmp_path, monkeypatch):
    src = make_source(tmp_path)
    out = tmp_path / "out"
    captured = {}

    def fake_pdf(html_path, out_pdf, **kw):
        captured["html"] = Path(html_path).read_text(encoding="utf-8")
        out_pdf.write_bytes(b"%PDF-1.4 fake")
        from qqexport.export.pdf import PdfResult
        return PdfResult(out_pdf)

    monkeypatch.setattr(orchestrator.pdf_mod, "write_pdf", fake_pdf)
    rep = orchestrator.export_chats(
        src, ["c2c:u_a"], orchestrator.ExportOptions(
            formats={"txt", "html", "pdf"}, out_base=out, now=NOW,
            message_ids={1}))
    folder = out / orchestrator.ROOT_FOLDER / "昵称A-987654321"
    assert {p.name for p in folder.iterdir()} == {
        "chat.txt", "chat.pdf", "chat.html"}
    text = (folder / "chat.txt").read_text(encoding="utf-8-sig")
    html = (folder / "chat.html").read_text(encoding="utf-8")
    assert "你好" in text and "你好" in captured["html"]
    assert "看图" not in text and "看图" not in captured["html"]
    assert "昵称A" in text and "昵称A" in captured["html"]
    assert rep.chats_exported == 1 and rep.messages_total == 1


def test_stale_temp_and_backup_dirs_are_cleaned(tmp_path):
    src = make_source(tmp_path)
    out = tmp_path / "out"
    root = out / orchestrator.ROOT_FOLDER
    root.mkdir(parents=True)
    dead_tmp = root / ".tmp-99999999-1"
    dead_old = root / "旧目录.old-99999999-1"
    live_tmp = root / f".tmp-{__import__('os').getpid()}-1"
    for path in (dead_tmp, dead_old, live_tmp):
        path.mkdir()
        (path / "marker").write_text("x", encoding="utf-8")
    orchestrator.export_chats(
        src, ["c2c:u_a"], orchestrator.ExportOptions(
            format="txt", out_base=out, now=NOW))
    assert not dead_tmp.exists()
    assert not dead_old.exists()
    assert live_tmp.exists()
    shutil.rmtree(live_tmp, ignore_errors=True)


def test_system_only_c2c_chat_is_hidden(tmp_path):
    normal = sd.text_row(ts=100, text="hi", uid="u_a")
    system = sd.text_row(ts=200, text="x", uid="u_sys", uin="987654321",
                         outer=1, sub=0)
    system["40800"] = None
    db = sd.create_msg_db(tmp_path / "nt.db", c2c=[normal, system])
    c = contacts.Contacts.load(None, None)
    src = ExportSource.from_parts(db, c, media.build_index(tmp_path / "none"),
                                  "123456789")
    ids = [x.chat_id for x in src.chats()]
    assert "c2c:u_a" in ids
    assert "c2c:u_sys" not in ids
