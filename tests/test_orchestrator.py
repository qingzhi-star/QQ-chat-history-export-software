"""导出编排测试：单格式、固定大目录、名称-账号、无多余文件。"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from qqexport.export import orchestrator
from qqexport.export.session import ExportSource
from qqexport.qq import contacts, media
from tests.helpers import synth_db as sd

NOW = datetime(2026, 10, 30, 12, 0, 0)


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


def test_system_only_c2c_chat_is_hidden(tmp_path):
    normal = sd.text_row(ts=100, text="hi", uid="u_a")
    system = sd.text_row(ts=200, text="x", uid="u_sys", uin="2909288299",
                         outer=1, sub=0)
    system["40800"] = None
    db = sd.create_msg_db(tmp_path / "nt.db", c2c=[normal, system])
    c = contacts.Contacts.load(None, None)
    src = ExportSource.from_parts(db, c, media.build_index(tmp_path / "none"),
                                  "123456789")
    ids = [x.chat_id for x in src.chats()]
    assert "c2c:u_a" in ids
    assert "c2c:u_sys" not in ids
