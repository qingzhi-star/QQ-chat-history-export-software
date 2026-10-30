#!/usr/bin/env python3
"""T1 探测工具：打印伴随库列的值形状分布（digits/uid/text/binary/null），
永不打印列值本体。

用法：
    python tools/probe_contacts.py --db path/to/profile_info.db [--limit 200]
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import Counter
from pathlib import Path


def value_shape(v: object) -> str:
    if v is None:
        return "null"
    if isinstance(v, (bytes, bytearray)):
        return "binary"
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, (int, float)):
        return "number"
    s = str(v)
    if not s:
        return "empty"
    if s.isdigit():
        return "digits"
    if s.startswith("u_"):
        return "uid"
    if len(s) <= 64:
        return "text"
    return "long_text"


def column_stats(con: sqlite3.Connection, table: str, limit: int) -> dict[str, Counter]:
    out: dict[str, Counter] = {}
    cols = [r[1] for r in con.execute(f'PRAGMA table_info("{table}")')]
    for col in cols:
        out[col] = Counter()
    q = f'SELECT * FROM "{table}" LIMIT {int(limit)}'
    for row in con.execute(q):
        for col, value in zip(cols, row):
            out[col][value_shape(value)] += 1
    return out


def describe(con: sqlite3.Connection, limit: int) -> dict[str, dict[str, dict]]:
    tables = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    result: dict[str, dict[str, dict]] = {}
    for table in tables:
        try:
            stats = column_stats(con, table, limit)
        except sqlite3.Error:
            continue
        result[table] = {col: dict(counter) for col, counter in stats.items()}
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--limit", type=int, default=200)
    args = ap.parse_args(argv)
    if not args.db.is_file():
        print(f"找不到文件：{args.db}", file=sys.stderr)
        return 2
    con = sqlite3.connect(args.db.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        for table, cols in describe(con, args.limit).items():
            print(f"\n=== {table} ===")
            for col, shapes in cols.items():
                pairs = ", ".join(f"{k}:{v}" for k, v in sorted(shapes.items()))
                print(f"  {col}: {pairs}")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
