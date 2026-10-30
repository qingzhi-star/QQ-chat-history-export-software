"""CLI 子命令解析（单格式 + GUI）。"""
from __future__ import annotations

from qqexport import cli


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
