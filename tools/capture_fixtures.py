#!/usr/bin/env python3
"""从真实 QQ 数据库截取页级 fixture。

隐私红线（spec §10.1、修正案 A §11）：

- 只截取「1024 字节 QQ NT 自定义头 + 加密页 1」。页 1 只含 `sqlite_master`
  与表结构，不含消息正文。
- 落盘**前**必须打印页 1 解密后的**全部**可打印字符串（不截断），并由人工
  逐条确认；确认短语是 `I HAVE REVIEWED THE STRINGS`，输入 `yes` 不算。
- 没有缓存口令时直接中止：无法审查字符串就不允许落盘（不得降低隐私门槛）。
- `provenance.json` 只保留复现必需的信息：日期（不含时分秒）、QQ 版本、
  页数、截取字节数、cipher 指纹。**不记录 uin、路径、key 指纹、源库大小。**

用法（需要先在 `qqexport key scan` 或 `qqexport key set` 里缓存口令）：

    python tools/capture_fixtures.py [--data-dir DIR] [--uin UIN]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qqexport.core import discover as disc          # noqa: E402
from qqexport.core import keystore                  # noqa: E402
from qqexport.core import sqlcipher as sc           # noqa: E402

OUT_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
CONFIRM_PHRASE = "I HAVE REVIEWED THE STRINGS"


def printable_strings(data: bytes, *, min_len: int = 4) -> list[str]:
    """把字节串切成可打印 ASCII 片段，返回**全部**长度 >= min_len 的片段。

    以不可打印字节（含 NUL）为分隔符，保持出现顺序，不截断长串。
    页 1 里的 schema 文本、表名、列名由此完整暴露，供人工逐条审查。
    """
    out: list[str] = []
    cur = bytearray()
    for byte in data:
        if 32 <= byte < 127:
            cur.append(byte)
        else:
            if len(cur) >= min_len:
                out.append(cur.decode("ascii"))
            cur.clear()
    if len(cur) >= min_len:
        out.append(cur.decode("ascii"))
    return out


def build_provenance(*, captured_at: str, qq_version: str | None,
                     source_page_count: int, captured_bytes: int,
                     cipher_fingerprint: str | None) -> dict:
    """最小化的 provenance（修正案 A §11.3）。绝不接受 uin / 路径 / key 指纹。"""
    return {
        "captured_at": captured_at,
        "qq_version": qq_version,
        "source_page_count": source_page_count,
        "has_wrapper_header": True,
        "captured_bytes": captured_bytes,
        "cipher_fingerprint": cipher_fingerprint,
    }


def _summarise(blob: bytes, account, params: sc.CipherParams) -> str:
    db = account.msg_db
    size = db.stat().st_size
    head = blob[:sc.WRAPPER_SZ]
    markers = [m for m in ("QQ_NT DB", "key_meta", "QQNT") if m.encode() in head]
    return "\n".join([
        f"  账号            : {disc.mask_uin(account.uin)}",
        f"  源库文件名      : {db.name}",
        f"  源库大小        : {size:,} B（{sc.page_count_from_size(size):,} 页）",
        f"  QQ 版本         : {disc.qq_install_version() or '未知'}",
        f"  采集日期        : {datetime.now().astimezone().date().isoformat()}",
        f"  截取字节数      : {len(blob):,}（1024 头 + 4096 加密页 1）",
        f"  自定义头可见标记: {markers or '无'}",
        f"  cipher 指纹     : {params.fingerprint}",
    ])


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="从真实 QQ 数据库截取页级 fixture")
    p.add_argument("--data-dir", type=Path, default=None)
    p.add_argument("--uin", default=None)
    args = p.parse_args(argv)

    try:
        accounts = disc.discover_accounts(explicit=args.data_dir)
        account = disc.select_account(accounts, args.uin)
    except disc.DiscoveryError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1

    try:
        blob = sc.read_header_and_page1(account.msg_db)
    except OSError as exc:
        print(f"错误：读取 nt_msg.db 失败（{exc.__class__.__name__}）",
              file=sys.stderr)
        return 1
    if len(blob) != sc.WRAPPER_SZ + sc.PAGE_SZ:
        print(f"错误：只读到 {len(blob)} 字节（需要 {sc.WRAPPER_SZ + sc.PAGE_SZ}），"
              "源库可能过小或被占用。", file=sys.stderr)
        return 1

    try:
        passphrase = keystore.KeyStore().load(account.uin)
    except keystore.KeyStoreCorrupt as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    if passphrase is None:
        print("错误：没有缓存口令，无法在落盘前审查页 1 的全部可打印字符串。\n"
              "请先运行 `qqexport key scan` 或 `qqexport key set` 后重试；"
              "无法审查就不得落盘（隐私门槛不降低）。", file=sys.stderr)
        return 1

    params = sc.detect_params(passphrase, blob)
    if params is None:
        print("错误：缓存口令无法认证该库的页 1（口令不对，或 QQ 改动了"
              "加密参数）。请重新 `qqexport key scan`。", file=sys.stderr)
        return 1
    plain = sc.decrypt_page(params, blob[sc.WRAPPER_SZ:], 1)
    if plain is None or not plain.startswith(sc.SQLITE_MAGIC):
        print("错误：页 1 解密失败或缺少 SQLite 魔数。", file=sys.stderr)
        return 1

    strings = printable_strings(plain)
    print("即将写入 tests/fixtures/nt_msg_header_page1.bin：")
    print(_summarise(blob, account, params))
    print(f"\n页 1 解密后的全部可打印字符串（共 {len(strings)} 条，"
          "长度 >= 4 的片段，未截断）：")
    for i, text in enumerate(strings, 1):
        print(f"  [{i:03d}] {text}")
    print()
    print("逐条确认：如果出现账号、路径、昵称、消息正文或任何自定义字段名，"
          "必须取消（不要提交）。")
    try:
        answer = input(f"确认无隐私问题后，输入 {CONFIRM_PHRASE} 继续：").strip()
    except EOFError:
        answer = ""
    if answer != CONFIRM_PHRASE:
        print("已取消，未写入任何文件。")
        return 0

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "nt_msg_header_page1.bin").write_bytes(blob)
    provenance = build_provenance(
        captured_at=datetime.now().astimezone().date().isoformat(),
        qq_version=disc.qq_install_version(),
        source_page_count=sc.page_count_from_size(account.msg_db.stat().st_size),
        captured_bytes=len(blob),
        cipher_fingerprint=params.fingerprint)
    (OUT_DIR / "provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")

    print(f"\n已写入 {OUT_DIR}")
    print("这两个文件默认被 .gitignore 排除。人工确认后提交：")
    print("    git add -f tests/fixtures/nt_msg_header_page1.bin "
          "tests/fixtures/provenance.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
