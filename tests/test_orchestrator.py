"""T11：编排器测试（合成 source，不碰真实数据）。"""
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
        rows={"group_list": [{"60001": 111, "60007": "群名"}]})
    c = contacts.Contacts.load(profile, group, account_uin="123456789")
    import tests.helpers.synth_proto as sp
    row = sd.text_row(ts=100, text="你好", uid="u_a")
    row2 = sd.text_row(ts=200, text="看图", uid="u_a", msg_id=2)
    row2["40800"] = sp.message(sp.image_element(md5=bytes.fromhex(md5),
                                                filename=f"{md5}.jpg"))
    db = sd.create_msg_db(tmp_path / "nt_msg.db", c2c=[row, row2])
    return ExportSource.from_parts(db, c, media.build_index(media_dir), "123456789")


def test_slug_is_path_safe():
    assert "/" not in orchestrator.sanitize_component("../../etc/passwd")
    assert "\\" not in orchestrator.sanitize_component("..\\..\\x")
    assert orchestrator.sanitize_component("CON") .startswith("_")
    assert orchestrator.sanitize_component("") == "chat"


def test_export_writes_txt_html_media_and_report(tmp_path):
    src = make_source(tmp_path)
    out = tmp_path / "out"
    rep = orchestrator.export_chats(
        src, ["c2c:u_a"], orchestrator.ExportOptions(
            formats={"txt", "html"}, out_dir=out, now=NOW))
    assert rep.messages_total == 2
    assert rep.messages_ok == 2 and rep.consistent
    assert rep.media_found == 1 and rep.media_missing == 0
    assert (out / "report.json").is_file()
    slug = orchestrator.chat_slug(src.chats()[0])
    assert (out / "123456789" / slug / "chat.txt").is_file()
    assert (out / "123456789" / slug / "chat.html").is_file()
    assert (out / "123456789" / slug / "media").is_dir()
    assert (out / "index.html").is_file()
    text = (out / "123456789" / slug / "chat.html").read_text(encoding="utf-8")
    assert "media/" in text and "http" not in text


def test_export_all_empty_no_crash(tmp_path):
    src = make_source(tmp_path)
    rep = orchestrator.export_chats(src, ["group:999"], orchestrator.ExportOptions(
        formats={"txt"}, out_dir=tmp_path / "out2", now=NOW))
    assert rep.chats_requested == 1 and rep.chats_exported == 0
    assert rep.warnings
