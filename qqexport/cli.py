"""命令行入口。参照 spec §9.1、修正案 A §10。

口令本体绝不打印；所有输出只用 SHA-256 指纹前 8 位。

安全与兼容要点：

- `key set` **不接受位置参数**（会进 shell history、`ps` 输出与诊断采集）。
  只支持交互式 `getpass` 与 `--from-stdin`（读一行即弃，不落临时文件）。
- `getpass` 在无法关闭回显时会退化为读 stdin 并发 `GetPassWarning`：
  捕获后提示改用 `--from-stdin` 并**中止**，不在回显失效下继续。
- `_force_utf8_stdio()` 失败必须安全回退（不得让命令终止）；中文 Windows
  控制台常是 cp936，emoji 会直接 UnicodeEncodeError。
- 所有需要账号的子命令走 `discover.select_account`：多账号必须显式 `--uin`，
  歧义时打印带掩码的候选列表并退出 1。
- 退出码：0 成功 / 1 操作失败或 doctor 有 fail / 2 用法错误（argparse）。
"""
from __future__ import annotations

import argparse
import getpass
import json
import sys
import warnings
from pathlib import Path

from . import __version__, config, doctor, report
from .core import discover, keyscan, keystore, sqlcipher
from .export import orchestrator, session

EXIT_OK = 0
EXIT_FAIL = 1


def _force_utf8_stdio() -> None:
    """中文 Windows 控制台常是 cp936，emoji 会直接 UnicodeEncodeError。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass                      # 被替换成无 reconfigure 的对象时安全回退


def _add_account_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--data-dir", type=Path, default=None,
                   help="手动指定 'Tencent Files' 目录")
    p.add_argument("--uin", default=None,
                   help="目标 QQ 号（发现多个账号时必须显式指定）")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="qqexport",
        description="导出本机自有 QQ NT 账号的聊天记录（仅限本人自有数据）。")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("version", help="打印版本号")

    d = sub.add_parser("doctor", help="11 项全链路自检")
    _add_account_args(d)
    d.add_argument("--scan", action="store_true",
                   help="允许被动内存扫描取口令（只读，不注入）")
    d.add_argument("--scan-drives", action="store_true", dest="scan_drives",
                   help="允许兜底递归扫描盘符定位数据目录（默认不扫）")
    d.add_argument("--timeout", type=float, default=keyscan.DEFAULT_TIMEOUT_S,
                   help="内存扫描墙钟超时秒数")
    d.add_argument("--json", action="store_true", help="以 JSON 输出")
    d.add_argument("--ascii", action="store_true", dest="ascii_only",
                   help="状态标记用纯 ASCII（供 cp936 控制台）")
    d.add_argument("--keep-output", action="store_true",
                   help="保留解密出的明文库（默认自检后删除）")

    k = sub.add_parser("key", help="口令管理")
    ksub = k.add_subparsers(dest="key_command", required=True)

    kscan = ksub.add_parser("scan", help="被动内存扫描取口令")
    _add_account_args(kscan)
    kscan.add_argument("--timeout", type=float, default=keyscan.DEFAULT_TIMEOUT_S)

    kset = ksub.add_parser("set", help="手动写入口令（交互式或 --from-stdin）")
    _add_account_args(kset)
    kset.add_argument("--from-stdin", action="store_true",
                      help="从标准输入读一行口令（供管道/自动化）")

    ksub.add_parser("show", help="列出已缓存口令的指纹（不含本体）")

    kclear = ksub.add_parser("clear", help="删除缓存口令")
    _add_account_args(kclear)

    lst = sub.add_parser("list", help="列出本机会话（标题为可读显示名）")
    _add_account_args(lst)

    exp = sub.add_parser("export", help="导出会话为 txt/html/pdf")
    _add_account_args(exp)
    exp.add_argument("--chat", action="append", default=[],
                     help="会话 ID（可重复），如 c2c:u_xxx / group:123")
    exp.add_argument("--all", action="store_true", help="导出全部会话")
    exp.add_argument("--format", default="html", choices=["txt", "html", "pdf"],
                     help="单格式：txt / html / pdf（默认 html）")
    exp.add_argument("--out", type=Path, default=Path("."),
                     help="导出位置（会在其下创建 QQ聊天记录导出）")
    exp.add_argument("--encoding", default="utf-8", help="txt 编码：utf-8 / gbk")
    exp.add_argument("--allow-large-pdf", action="store_true",
                     help="允许 >30000 条消息生成 PDF")

    gui = sub.add_parser("gui", help="打开图形界面（选择会话/格式/导出位置）")
    _add_account_args(gui)

    return p


def _discover_fail(exc: discover.DiscoveryError) -> int:
    print(f"错误：{exc}", file=sys.stderr)
    print("提示：用 --uin 指定要使用的账号，或用 --data-dir 指定 "
          "'Tencent Files' 目录。", file=sys.stderr)
    return EXIT_FAIL


def _pick_account(args) -> discover.QQAccount:
    accounts = discover.discover_accounts(explicit=args.data_dir)
    if args.uin:
        return discover.select_account(accounts, args.uin)
    try:
        return discover.detect_active_account(accounts)
    except discover.AmbiguousAccountError:
        uin = config.Config.load().uin
        return discover.select_account(accounts, uin)


def _read_passphrase(args) -> str | None:
    """读口令。回显无法关闭时提示并返回 None（不静默继续）。"""
    if args.from_stdin:
        return sys.stdin.readline().rstrip("\r\n")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        text = getpass.getpass("请输入数据库口令（不回显）：")
    if any(issubclass(w.category, getpass.GetPassWarning) for w in caught):
        print("错误：当前终端无法关闭回显，口令会显示在屏幕上。"
              "请改用 --from-stdin，或在真实终端中运行。", file=sys.stderr)
        return None
    return text


def _cmd_doctor(args) -> int:
    work_dir = config.app_dir() / "work" / "doctor" if args.keep_output else None
    try:
        rep = doctor.run_doctor(
            data_dir=args.data_dir, uin=args.uin, allow_scan=args.scan,
            scan_timeout_s=args.timeout, work_dir=work_dir,
            keep_output=args.keep_output, scan_drives=args.scan_drives)
    except ValueError as exc:          # keep_output 缺少可用 work_dir 等
        print(f"错误：{exc}", file=sys.stderr)
        return EXIT_FAIL
    if args.json:
        print(json.dumps(rep.to_json(), ensure_ascii=False, indent=2))
    else:
        print(rep.to_text(ascii_only=args.ascii_only))
    return EXIT_OK if rep.ok else EXIT_FAIL


def _cmd_key_scan(args) -> int:
    try:
        account = _pick_account(args)
    except discover.DiscoveryError as exc:
        return _discover_fail(exc)
    try:
        head = sqlcipher.read_header_and_page1(account.msg_db)
    except OSError as exc:
        print(f"错误：读取 nt_msg.db 失败（{exc.__class__.__name__}）",
              file=sys.stderr)
        return EXIT_FAIL
    try:
        found = keyscan.scan_key(
            lambda cand: sqlcipher.detect_params(cand, head) is not None,
            timeout_s=args.timeout)
    except keyscan.ProcessAccessDenied:
        print("错误：无权读取 QQ 进程内存。请以管理员身份重试，"
              "或改用 `qqexport key set` 手动写入。", file=sys.stderr)
        return EXIT_FAIL
    if found is None:
        print("未找到可用口令。请确认 QQ 正在运行并已登录，"
              "或改用 `qqexport key set` 手动写入。", file=sys.stderr)
        return EXIT_FAIL
    try:
        entry = keystore.KeyStore().save(account.uin, found)
    except (keystore.KeyStoreUnavailable, keystore.KeyStoreCorrupt) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return EXIT_FAIL
    print(f"已保存 uin={account.uin} 的口令，指纹 {entry.fingerprint}")
    return EXIT_OK


def _cmd_key_set(args) -> int:
    try:
        account = _pick_account(args)
    except discover.DiscoveryError as exc:
        return _discover_fail(exc)
    text = _read_passphrase(args)
    if text is None:
        return EXIT_FAIL
    raw = text.encode("utf-8")
    if not raw:
        print("错误：口令为空，未保存。", file=sys.stderr)
        return EXIT_FAIL
    if len(raw) not in keyscan.KEY_LENGTHS:
        print(f"警告：口令长度为 {len(raw)} 字节，而 QQ NT 通常为 "
              f"{keyscan.KEY_LENGTHS} 之一。仍将保存。", file=sys.stderr)
    try:
        entry = keystore.KeyStore().save(account.uin, raw)
    except (keystore.KeyStoreUnavailable, keystore.KeyStoreCorrupt) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return EXIT_FAIL
    print(f"已保存 uin={account.uin}，指纹 {entry.fingerprint}")
    return EXIT_OK


def _cmd_key_show(_args) -> int:
    try:
        entries = keystore.KeyStore().redacted_dump()
    except (keystore.KeyStoreCorrupt, keystore.KeyStoreUnavailable) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return EXIT_FAIL
    if not entries:
        print("没有已缓存的口令。用 `qqexport key scan` 或 "
              "`qqexport key set` 添加。")
        return EXIT_OK
    for e in entries:
        print(f"uin={e['uin']}  指纹={e['fingerprint']}  保存于={e['saved_at']}")
    return EXIT_OK


def _cmd_key_clear(args) -> int:
    try:
        account = _pick_account(args)
    except discover.DiscoveryError as exc:
        return _discover_fail(exc)
    try:
        n = keystore.KeyStore().clear(account.uin)
    except (keystore.KeyStoreUnavailable, keystore.KeyStoreCorrupt) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return EXIT_FAIL
    print(f"已删除 {n} 条缓存口令" if n else "没有匹配的缓存口令")
    return EXIT_OK


def _open_source(args) -> session.ExportSource | None:
    try:
        account = _pick_account(args)
    except discover.DiscoveryError as exc:
        _discover_fail(exc)
        return None
    key = keystore.KeyStore().load(account.uin)
    if not key:
        print("错误：没有缓存口令。先运行 `qqexport key scan` 或 "
              "`qqexport key set`。", file=sys.stderr)
        return None
    try:
        return session.ExportSource.open(account, key)
    except Exception as exc:
        print(f"错误：打开数据源失败（{type(exc).__name__}）", file=sys.stderr)
        return None


def _cmd_list(args) -> int:
    source = _open_source(args)
    if source is None:
        return EXIT_FAIL
    try:
        chats = source.chats()
        if not chats:
            print("没有可导出的会话。")
            return EXIT_OK
        for c in chats:
            first = c.first_ts or 0
            print(f"{c.chat_id}\t{c.messages}\t{first}\t{c.title}")
        return EXIT_OK
    finally:
        source.close()


def _cmd_export(args) -> int:
    if args.all and args.chat:
        print("错误：--all 与 --chat 不能同时使用。", file=sys.stderr)
        return EXIT_FAIL
    formats = {f.strip().lower() for f in args.format.split(",") if f.strip()}
    if len(formats) != 1 or formats - {"txt", "html", "pdf"}:
        print("错误：--format 只能选一个：txt / html / pdf。", file=sys.stderr)
        return EXIT_FAIL
    source = _open_source(args)
    if source is None:
        return EXIT_FAIL
    try:
        chat_ids = [c.chat_id for c in source.chats()] if args.all else list(args.chat)
        if not chat_ids:
            print("错误：请用 --chat 指定会话，或用 --all。", file=sys.stderr)
            return EXIT_FAIL
        rep = orchestrator.export_chats(
            source, chat_ids, orchestrator.ExportOptions(
                format=formats.pop(), out_base=args.out,
                encoding=args.encoding, allow_large_pdf=args.allow_large_pdf,
                ))
        print(rep.to_text())
        for d in rep.exported_dirs:
            print(f"已导出：{d}")
        return EXIT_OK if rep.chats_exported else EXIT_FAIL
    finally:
        source.close()


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdio()
    args = build_parser().parse_args(argv)

    if args.command == "version":
        print(f"qqexport {__version__}")
        return EXIT_OK
    if args.command == "doctor":
        return _cmd_doctor(args)
    if args.command == "list":
        return _cmd_list(args)
    if args.command == "export":
        return _cmd_export(args)
    if args.command == "gui":
        from .gui import run_gui
        return run_gui(data_dir=args.data_dir, uin=args.uin)
    handler = {"scan": _cmd_key_scan, "set": _cmd_key_set,
               "show": _cmd_key_show, "clear": _cmd_key_clear}
    return handler[args.key_command](args)


if __name__ == "__main__":
    raise SystemExit(main())
