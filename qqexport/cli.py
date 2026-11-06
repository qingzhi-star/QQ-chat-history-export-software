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

from . import __version__, config, doctor, report, selection
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


def _parse_formats(text: str) -> str:
    formats = [f.strip().lower() for f in text.split(",")]
    if any(f not in {"txt", "html", "pdf"} for f in formats):
        raise argparse.ArgumentTypeError("格式应为 txt/html/pdf，多个用逗号分隔")
    return ",".join(formats)


def _parse_date(text: str, *, end: bool = False) -> int:
    try:
        ts = selection.day_timestamp(text, end=end)
        if ts is None:
            raise ValueError("日期不能为空")
        return ts
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


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

    search = sub.add_parser("search", help="本机会话/全库内存搜索，不持久化正文索引")
    _add_account_args(search)
    search.add_argument("--query",default="")
    search.add_argument("--chat",default=None)
    search.add_argument("--sender",default=None)
    search.add_argument("--type",dest="kind",default=None)
    search.add_argument("--start",type=_parse_date,default=None)
    search.add_argument("--end",type=lambda s:_parse_date(s,end=True),default=None)
    search.add_argument("--match",choices=["contains","exact","regex"],default="contains")
    search.add_argument("--limit",type=int,default=200)
    search.add_argument("--offset",type=int,default=0)

    batch = sub.add_parser("batch",help="可取消/恢复的批量导出队列")
    _add_account_args(batch)
    targets = batch.add_mutually_exclusive_group(required=True)
    targets.add_argument("--chat",action="append",default=[])
    targets.add_argument("--all",action="store_true")
    targets.add_argument("--resume",type=Path)
    batch.add_argument("--yes",action="store_true",help="确认导出全部会话")
    batch.add_argument("--format",type=_parse_formats,default=None)
    batch.add_argument("--out",type=Path,default=None)
    batch.add_argument("--checkpoint",type=Path,default=None)
    batch.add_argument("--manifest",action="store_true")
    batch.add_argument("--retry-failed",action="store_true")

    exp = sub.add_parser("export", help="导出会话为 txt/html/pdf")
    _add_account_args(exp)
    target = exp.add_mutually_exclusive_group(required=True)
    target.add_argument("--chat", action="append", default=[],
                        help="会话 ID（可重复），如 c2c:u_xxx / group:123")
    target.add_argument("--all", action="store_true", help="导出全部会话")
    exp.add_argument("--format", default="html", type=_parse_formats,
                     help="txt / html / pdf，多个用逗号分隔（默认 html）")
    exp.add_argument("--start", type=_parse_date, default=None,
                     help="开始日期 YYYY-MM-DD（含本地当天，不指定则不限）")
    exp.add_argument("--end", type=lambda s: _parse_date(s, end=True), default=None,
                     help="结束日期 YYYY-MM-DD（含本地当天，不指定则不限）")
    exp.add_argument("--message-id", type=int, action="append", default=None,
                     help="消息 ID（可重复，仅限一个 --chat；与日期范围取交集）")
    exp.add_argument("--out", type=Path, default=Path("."),
                     help="导出位置（会在其下创建 QQ聊天记录导出）")
    exp.add_argument("--encoding", default="utf-8", choices=["utf-8", "gbk"],
                     help="txt 编码：utf-8 / gbk")
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
    formats = set(args.format.split(","))
    source = _open_source(args)
    if source is None:
        return EXIT_FAIL
    try:
        chat_ids = ([c.chat_id for c in source.chats()] if args.all
                    else list(dict.fromkeys(args.chat)))
        if not chat_ids:
            print("错误：没有可导出的会话。", file=sys.stderr)
            return EXIT_FAIL
        rep = orchestrator.export_chats(
            source, chat_ids, orchestrator.ExportOptions(
                formats=formats, out_base=args.out,
                encoding=args.encoding, allow_large_pdf=args.allow_large_pdf,
                start_ts=args.start, end_ts=args.end,
                message_ids=(set(args.message_id)
                             if args.message_id is not None else None),
                ))
        print(rep.to_text())
        for d in rep.exported_dirs:
            print(f"已导出：{d}")
        return (EXIT_OK if rep.chats_exported == len(chat_ids) and not rep.pdf_error
                else EXIT_FAIL)
    except Exception as exc:
        print(f"错误：导出失败（{type(exc).__name__}）", file=sys.stderr)
        return EXIT_FAIL
    finally:
        source.close()


def _cmd_search(args):
    from qqexport.repository import MessageRepository
    from qqexport.search import SearchIndex,Query
    source = _open_source(args)
    if source is None:
        return EXIT_FAIL
    repo = index = None
    try:
        repo = MessageRepository(source)
        index = SearchIndex()
        index.build(repo)
        query = Query(args.query,args.chat,args.sender,args.kind,args.start,args.end,args.match)
        for hit in index.find(query,limit=args.limit,offset=args.offset):
            print(f"{hit.chat_id}\t{hit.msg_id}\t{hit.ts}\t{hit.sender}\t{hit.summary}")
        return EXIT_OK
    except Exception as exc:
        print(f"错误：搜索失败（{type(exc).__name__}）",file=sys.stderr)
        return EXIT_FAIL
    finally:
        if index is not None:
            index.close()
        if repo is not None:
            repo.close()
        source.close()


def _cmd_batch(args):
    from qqexport.export.jobs import ExportJob
    source = _open_source(args)
    if source is None:
        return EXIT_FAIL
    job = None
    try:
        if args.resume:
            job = ExportJob.load(args.resume,source.account_uin)
            if args.retry_failed:
                job.retry_failed()
        else:
            ids = [c.chat_id for c in source.chats()] if args.all else args.chat
            job = ExportJob(source.account_uin,ids,orchestrator.ExportOptions(
                formats=set((args.format or "html").split(",")),out_base=args.out or Path(".")),
                checkpoint=args.checkpoint,write_manifest=args.manifest)
        state = job.run(source)
        counts = {status:sum(t["status"]==status for t in state["tasks"])
                  for status in ("done","failed","skipped","pending","cancelled")}
        print(json.dumps(counts,ensure_ascii=False))
        return EXIT_FAIL if any(counts[s] for s in ("failed","pending","cancelled")) else EXIT_OK
    except KeyboardInterrupt:
        if job:
            job.cancel.set()
        print("已取消；已完成产物保留。",file=sys.stderr)
        return EXIT_FAIL
    except Exception as exc:
        print(f"错误：队列失败（{type(exc).__name__}）",file=sys.stderr)
        return EXIT_FAIL
    finally:
        source.close()


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "batch":
        if args.all and not args.yes:
            parser.error("导出全部会话请加 --yes 明确确认")
        if args.resume and any((args.format,args.out,args.checkpoint,args.manifest)):
            parser.error("恢复队列不能覆盖其格式/输出/检查点参数")
    if args.command == "search":
        if not 1<=args.limit<=1000 or args.offset<0 or len(args.query)>1000:
            parser.error("搜索页大小必须为 1–1000，位置非负，关键词最多 1000 字符")
        if args.start is not None and args.end is not None and args.start>args.end:
            parser.error("开始日期不能晚于结束日期")
    if args.command == "export":
        if args.start is not None and args.end is not None and args.start > args.end:
            parser.error("开始日期不能晚于结束日期")
        if args.message_id is not None and (args.all or len(set(args.chat)) != 1):
            parser.error("--message-id 仅可与一个 --chat 会话一起使用")
    try:
        return _dispatch(args)
    except config.ConfigError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return EXIT_FAIL


def _dispatch(args) -> int:
    if args.command == "version":
        print(f"qqexport {__version__}")
        return EXIT_OK
    if args.command == "doctor":
        return _cmd_doctor(args)
    if args.command == "list":
        return _cmd_list(args)
    if args.command == "search":
        return _cmd_search(args)
    if args.command == "batch":
        return _cmd_batch(args)
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
