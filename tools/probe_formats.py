#!/usr/bin/env python3
"""T1 探测工具：打印明文库里 40800 的 wire 结构（字段号/长度），永不打印内容。

用法：
    python tools/probe_formats.py --db path/to/nt_msg.db [--limit 200]
    python tools/probe_formats.py --db ... --table group_msg_table

只读：以 mode=ro URI 打开。输出仅字段号、wire type、长度与元素个数。
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import Counter
from pathlib import Path


def read_varint(buf: bytes, i: int) -> tuple[int, int]:
    shift = 0
    val = 0
    while True:
        if i >= len(buf):
            raise ValueError("truncated varint")
        b = buf[i]
        i += 1
        val |= (b & 0x7F) << shift
        if not b & 0x80:
            return val, i
        shift += 7
        if shift > 63:
            raise ValueError("varint too long")


def wire_fields(buf: bytes) -> list[tuple[int, int, int | bytes, bool]]:
    """返回 [(field, wire_type, value_or_len, ok)]；ok=False 表示在此截断。"""
    out: list[tuple[int, int, int | bytes, bool]] = []
    i = 0
    while i < len(buf):
        try:
            key, i = read_varint(buf, i)
            f, wt = key >> 3, key & 7
            if wt == 0:
                v, i = read_varint(buf, i)
                out.append((f, wt, v, True))
            elif wt == 1:
                if i + 8 > len(buf):
                    raise ValueError
                out.append((f, wt, 8, True))
                i += 8
            elif wt == 2:
                ln, i = read_varint(buf, i)
                if i + ln > len(buf):
                    raise ValueError
                out.append((f, wt, ln, True))
                i += ln
            elif wt == 5:
                if i + 4 > len(buf):
                    raise ValueError
                out.append((f, wt, 4, True))
                i += 4
            else:
                out.append((f, wt, 0, False))
                break
        except ValueError:
            out.append((-1, -1, 0, False))
            break
    return out


def summarize_blob(blob: bytes) -> dict:
    """只输出结构摘要，不包含任何 payload 字节。"""
    top = wire_fields(blob)
    elements: list[dict] = []
    i = 0
    while i < len(blob):
        try:
            key, i = read_varint(blob, i)
            f, wt = key >> 3, key & 7
            if wt == 2:
                ln, i = read_varint(blob, i)
                payload = blob[i:i + ln]
                i += ln
                if f == 40800:
                    children = wire_fields(payload)
                    elements.append({
                        "child_fields": sorted({c[0] for c in children if c[0] >= 0}),
                        "child_wires": sorted({(c[0], c[1]) for c in children
                                               if c[0] >= 0}),
                        "child_len": len(payload),
                        "truncated": not all(c[3] for c in children),
                    })
            elif wt == 0:
                _, i = read_varint(blob, i)
            elif wt == 1:
                i += 8
            elif wt == 5:
                i += 4
            else:
                break
        except ValueError:
            break
    return {
        "top_fields": sorted({f for f, wt, _, ok in top if ok and f >= 0}),
        "elements": elements,
        "truncated": not all(ok for *_, ok in top),
    }


def iter_rows(con: sqlite3.Connection, table: str, limit: int):
    q = f'SELECT "40011","40012","40800" FROM "{table}" WHERE "40800" IS NOT NULL LIMIT {int(limit)}'
    for outer, sub, blob in con.execute(q):
        yield outer, sub, bytes(blob)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--table", action="append", default=None)
    ap.add_argument("--limit", type=int, default=200)
    args = ap.parse_args(argv)
    if not args.db.is_file():
        print(f"找不到文件：{args.db}", file=sys.stderr)
        return 2
    con = sqlite3.connect(args.db.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        tables = args.table or ["c2c_msg_table", "group_msg_table"]
        for table in tables:
            try:
                rows = list(iter_rows(con, table, args.limit))
            except sqlite3.Error as exc:
                print(f"{table}: 跳过（{exc}）")
                continue
            print(f"\n=== {table} 样本 {len(rows)} ===")
            groups: dict[tuple, Counter] = {}
            for outer, sub, blob in rows:
                summary = summarize_blob(blob)
                sig = tuple(
                    (tuple(e["child_fields"]), e["child_len"] // 64 * 64)
                    for e in summary["elements"])
                groups.setdefault((outer, sub), Counter())[sig] += 1
            for (outer, sub), counter in sorted(groups.items()):
                print(f"  40011={outer} 40012={sub}: {sum(counter.values())} 条")
                for sig, count in counter.most_common(3):
                    fields = sorted({f for child_fields, _ in sig
                                     for f in child_fields})
                    print(f"    {count:>4}× 元素数={len(sig)} 子字段={fields}")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
