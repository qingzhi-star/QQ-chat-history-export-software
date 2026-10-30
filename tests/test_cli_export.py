"""T11：新 CLI 子命令的解析测试。"""
from __future__ import annotations

from qqexport import cli


def test_export_parser_defaults():
    args = cli.build_parser().parse_args(["export", "--all"])
    assert args.format == "html"
    assert str(args.out) == "export"
    assert args.shard_size == 20000
    assert args.no_media is False


def test_export_parser_formats_and_chats():
    args = cli.build_parser().parse_args(
        ["export", "--chat", "c2c:u_a", "--chat", "group:1",
         "--format", "txt,html", "--out", "out", "--no-media"])
    assert args.chat == ["c2c:u_a", "group:1"]
    assert args.format == "txt,html"
    assert args.no_media is True


def test_list_parser_accepts_uin():
    args = cli.build_parser().parse_args(["list", "--uin", "123456789"])
    assert args.command == "list" and args.uin == "123456789"
