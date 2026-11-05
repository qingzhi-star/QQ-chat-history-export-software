"""T07：CLI 参数、合成数据导出及失败语义。"""
from __future__ import annotations

from datetime import datetime
import pytest

from qqexport import cli, config, selection
from qqexport.export import orchestrator
from qqexport.export.pdf import PdfResult
from qqexport.export.session import ExportSource
from qqexport.qq import contacts, media
from tests.helpers import synth_db as sd


def test_export_parser_defaults():
    args = cli.build_parser().parse_args(["export", "--all"])
    assert args.format == "html"
    assert str(args.out) == "."


def test_export_single_format_and_chats():
    args = cli.build_parser().parse_args(
        ["export", "--chat", "c2c:u_a", "--chat", "group:1", "--format", "txt"])
    assert args.chat == ["c2c:u_a", "group:1"]
    assert args.format == "txt"


def test_list_and_gui_parsers():
    assert cli.build_parser().parse_args(["list", "--uin", "123456789"]).command == "list"
    assert cli.build_parser().parse_args(["gui"]).command == "gui"


@pytest.fixture
def source(tmp_path, monkeypatch):
    monkeypatch.setenv("QQEXPORT_HOME", str(tmp_path / "home"))
    rows = [sd.text_row(ts=int(datetime.fromisoformat(stamp).timestamp()),
                        text=f"synthetic-message-{i}", msg_id=i, uid="u_a")
            for i, stamp in enumerate([
                "2026-11-04T23:59:59", "2026-11-05T00:00:00",
                "2026-11-05T12:00:00", "2026-11-05T23:59:59",
                "2026-11-06T00:00:00"], 1)]
    db = sd.create_msg_db(tmp_path / "nt_msg.db", c2c=rows, group=[
        sd.text_row(ts=rows[1]["40050"], text="synthetic-group", group="11111111")])
    src = ExportSource.from_parts(
        db, contacts.Contacts.load(None, None, account_uin="12345"),
        media.build_index(tmp_path / "nt_data"), "12345")
    monkeypatch.setattr(cli, "_open_source", lambda args: src)
    closed = []
    monkeypatch.setattr(src, "close", lambda: closed.append(True))
    src.closed_calls = closed
    return src


def exported_folder(tmp_path):
    folders = list((tmp_path / "out" / orchestrator.ROOT_FOLDER).iterdir())
    assert len(folders) == 1
    return folders[0]


def test_multiformat_dates_and_ids_share_gui_snapshot(source, tmp_path, monkeypatch):
    pdf_html = []
    queries = []
    original = source.iter_messages

    def read(chat_id):
        queries.append(chat_id)
        return original(chat_id)

    def pdf(html_path, out_pdf, **kw):
        pdf_html.append(html_path.read_text(encoding="utf-8"))
        out_pdf.write_bytes(b"%PDF-1.4 synthetic")
        return PdfResult(out_pdf)

    monkeypatch.setattr(source, "iter_messages", read)
    monkeypatch.setattr(orchestrator.pdf_mod, "write_pdf", pdf)
    argv = ["export", "--chat", "c2c:u_a", "--format", " TXT,html,pdf,txt ",
            "--start", "2026-11-05", "--end", "2026-11-05",
            "--out", str(tmp_path / "out")]
    for msg_id in [5, 4, 2, 1, 2]:
        argv.extend(["--message-id", str(msg_id)])
    assert cli.main(argv) == 0
    folder = exported_folder(tmp_path)
    assert {p.name for p in folder.iterdir()} == {"chat.txt", "chat.html", "chat.pdf"}
    html = (folder / "chat.html").read_text(encoding="utf-8")
    assert pdf_html == [html]
    txt = (folder / "chat.txt").read_text(encoding="utf-8-sig")
    for text in (txt, html):
        assert text.index("synthetic-message-2") < text.index("synthetic-message-4")
        for i in (1, 3, 5):
            assert f"synthetic-message-{i}" not in text
    assert queries == ["c2c:u_a"]
    assert source.closed_calls == [True]


@pytest.mark.parametrize(("extra", "expected"), [
    ([], [1, 2, 3, 4, 5]),
    (["--start", "2026-11-05"], [2, 3, 4, 5]),
    (["--end", "2026-11-05"], [1, 2, 3, 4]),
    (["--start", "2026-11-05", "--end", "2026-11-06"], [2, 3, 4, 5]),
    (["--start", "2026-11-05", "--end", "2026-11-05"], [2, 3, 4]),
    (["--message-id", "4", "--message-id", "2"], [2, 4]),
    (["--start", "2026-11-07"], []),
    (["--message-id", "99"], []),
])
def test_range_and_selection_outputs(source, tmp_path, extra, expected):
    assert cli.main(["export", "--chat", "c2c:u_a", "--format", "txt",
                     "--out", str(tmp_path / "out"), *extra]) == 0
    text = (exported_folder(tmp_path) / "chat.txt").read_text(encoding="utf-8-sig")
    assert [i for i in range(1, 6) if f"synthetic-message-{i}" in text] == expected
    # 与 GUI 的日期模型选择相同（空选择生成空文件，沿用编排器语义）。
    args = cli.build_parser().parse_args(["export", "--chat", "c2c:u_a", *extra])
    messages = list(source.iter_messages("c2c:u_a"))
    indices = selection.indices_in_range(messages, args.start, args.end)
    ids = [m.msg_id for i, m in enumerate(messages) if i in indices
           and (args.message_id is None or m.msg_id in args.message_id)]
    assert ids == expected


@pytest.mark.parametrize("target", [
    ["--all"], ["--chat", "c2c:u_a", "--chat", "group:11111111"],
])
def test_export_multiple_chats(source, tmp_path, target):
    assert cli.main(["export", *target, "--format", "txt,html",
                     "--out", str(tmp_path / "out")]) == 0
    folders = list((tmp_path / "out" / orchestrator.ROOT_FOLDER).iterdir())
    assert len(folders) == 2
    assert all({p.name for p in f.iterdir()} == {"chat.txt", "chat.html"} for f in folders)


def test_duplicate_chat_ids_export_once(source, tmp_path):
    assert cli.main(["export", "--chat", "c2c:u_a", "--chat", "c2c:u_a",
                     "--message-id", "2", "--format", "txt",
                     "--out", str(tmp_path / "out")]) == 0
    assert exported_folder(tmp_path).is_dir()


@pytest.mark.parametrize("options", [
    [], ["--all", "--chat", "c2c:u_a"],
    ["--all", "--format", "xml"], ["--all", "--format", "txt,"],
    ["--all", "--format", ""], ["--all", "--encoding", "bogus"],
    ["--all", "--start", "2026-02-30"], ["--all", "--end", "not-a-date"],
    ["--all", "--start", "2026-1-1"], ["--all", "--end", ""],
    ["--all", "--start", "2026-11-06", "--end", "2026-11-05"],
    ["--all", "--message-id", "1"],
    ["--chat", "c2c:u_a", "--chat", "group:11111111", "--message-id", "1"],
    ["--chat", "c2c:u_a", "--message-id", "not-an-id"],
])
def test_invalid_args_fail_before_opening_data(monkeypatch, options, capsys):
    def forbidden(args):
        pytest.fail("不合法参数不得打开 QQ 数据")

    monkeypatch.setattr(cli, "_open_source", forbidden)
    with pytest.raises(SystemExit) as exc:
        cli.main(["export", *options])
    assert exc.value.code == 2
    assert "Traceback" not in capsys.readouterr().err


def test_partial_pdf_failure_is_nonzero_and_keeps_other_formats(source, tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator.pdf_mod, "write_pdf",
                        lambda *a, **kw: PdfResult(None, error="synthetic failure"))
    assert cli.main(["export", "--chat", "c2c:u_a", "--format", "txt,pdf",
                     "--out", str(tmp_path / "out")]) == 1
    assert {p.name for p in exported_folder(tmp_path).iterdir()} == {"chat.txt"}
    assert source.closed_calls == [True]


def test_unknown_chat_is_nonzero_even_when_another_succeeds(source, tmp_path):
    assert cli.main(["export", "--chat", "c2c:u_a", "--chat", "c2c:u_missing",
                     "--format", "txt", "--out", str(tmp_path / "out")]) == 1
    assert source.closed_calls == [True]


def test_export_failure_closes_source_and_redacts_error(source, tmp_path, monkeypatch, capsys):
    def fail(*args):
        raise OSError("synthetic-private-content")

    monkeypatch.setattr(orchestrator, "export_chats", fail)
    assert cli.main(["export", "--all", "--out", str(tmp_path / "out")]) == 1
    assert source.closed_calls == [True]
    text = capsys.readouterr().err
    assert "OSError" in text and "synthetic-private-content" not in text
    assert "Traceback" not in text


def test_corrupt_config_is_reported_without_overwriting(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("QQEXPORT_HOME", str(tmp_path))
    path = config.config_path()
    raw = b'{"uin": "synthetic-private-content",'
    path.write_bytes(raw)
    monkeypatch.setattr(cli.discover, "discover_accounts", lambda **kw: [])

    def ambiguous(accounts):
        raise cli.discover.AmbiguousAccountError("synthetic")

    monkeypatch.setattr(cli.discover, "detect_active_account", ambiguous)
    assert cli.main(["export", "--all"]) == 1
    text = capsys.readouterr().err
    assert "config.json" in text and "损坏" in text
    assert "synthetic-private-content" not in text and "Traceback" not in text
    assert path.read_bytes() == raw
