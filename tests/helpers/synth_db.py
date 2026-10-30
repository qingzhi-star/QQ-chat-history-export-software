"""T1：合成 SQLite 库工厂（测试专用，内容全部虚构）。

消息表列名按 2026-10-29 探测的真实 39 列；伴随库只建 P1 用到的子集列。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from tests.helpers import synth_proto as sp

# 真实探测到的 c2c/group 公共列（顺序固定；测试只依赖其中一部分）
MSG_COLUMNS: tuple[str, ...] = (
    "40001", "40002", "40003", "40010", "40011", "40012", "40013", "40020",
    "40026", "40021", "40027", "40040", "40041", "40050", "40052", "40090",
    "40093", "40800", "40900", "40105", "40005", "40058", "40006", "40100",
    "40600", "40060", "40850", "40851", "40601", "40801", "40605", "40030",
    "40033", "40062", "40083", "40084", "40008", "40009", "40722",
)

_INT_COLUMNS = {"40001", "40002", "40003", "40010", "40011", "40012", "40013",
                "40040", "40041", "40050", "40052", "40090", "40093", "40900",
                "40105", "40005", "40058", "40006", "40100", "40600", "40060",
                "40850", "40851", "40601", "40801", "40605", "40062", "40083",
                "40084", "40008", "40009", "40722"}

COMPANION_SCHEMA: dict[str, dict[str, tuple[str, ...]]] = {
    "profile": {
        "profile_info_v6": ("1000", "1001", "1002", "20002", "20009"),
        "buddy_list": ("1000", "1001", "1002", "25007"),
        "profile_info_adelie": ("1000", "1002", "320001", "320002"),
    },
    "group": {
        "group_list": ("60001", "60002", "60004", "60005", "60007"),
        "group_detail_info_ver1": ("60001", "60007", "60216"),
        "group_member3": ("60001", "1000", "1001", "1002", "20002", "64003"),
    },
    "rich_media": {
        "file_table": ("40001", "45402", "45405", "45406", "45419"),
    },
}


def _create_table(con: sqlite3.Connection, name: str,
                  columns: tuple[str, ...]) -> None:
    defs = []
    for col in columns:
        typ = "INTEGER" if col in _INT_COLUMNS else "TEXT"
        if col == "40800":
            typ = "BLOB"
        defs.append(f'"{col}" {typ}')
    con.execute(f'CREATE TABLE "{name}" ({", ".join(defs)})')


def _insert(con: sqlite3.Connection, table: str, row: dict) -> None:
    keys = list(row)
    cols = ", ".join(f'"{k}"' for k in keys)
    marks = ", ".join("?" for _ in keys)
    con.execute(f'INSERT INTO "{table}" ({cols}) VALUES ({marks})',
                [row[k] for k in keys])


def create_msg_db(path: Path, *, c2c: list[dict] | None = None,
                  group: list[dict] | None = None,
                  drop_columns: list[str] | None = None) -> Path:
    drop = set(drop_columns or ())
    cols = tuple(c for c in MSG_COLUMNS if c not in drop)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    try:
        for table, rows in (("c2c_msg_table", c2c or []),
                            ("group_msg_table", group or [])):
            _create_table(con, table, cols)
            for row in rows:
                clean = {k: v for k, v in row.items() if k not in drop}
                _insert(con, table, clean)
        con.commit()
    finally:
        con.close()
    return path


def text_row(*, ts: int, text: str, group: str | None = None,
             uin: str = "123456789", uid: str = "u_testuser",
             msg_id: int = 1, direction: int = 0,
             outer: int = 2, sub: int = 1) -> dict:
    sender = uin
    row = {
        "40001": msg_id,
        "40011": outer,
        "40012": sub,
        "40013": direction,
        "40020": uid,
        "40021": group if group is not None else uid,
        "40030": "987654321",
        "40033": sender,
        "40050": ts,
        "40800": sp.message(sp.text_element(text)),
    }
    return row


def create_companion_db(path: Path, *, kind: str,
                        rows: dict[str, list[dict]] | None = None) -> Path:
    if kind not in COMPANION_SCHEMA:
        raise ValueError(f"unknown companion kind: {kind}")
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    try:
        for table, cols in COMPANION_SCHEMA[kind].items():
            _create_table(con, table, cols)
        for table, table_rows in (rows or {}).items():
            for row in table_rows:
                clean = {k: v for k, v in row.items()
                         if k in COMPANION_SCHEMA[kind].get(table, ())}
                if clean:
                    _insert(con, table, clean)
        con.commit()
    finally:
        con.close()
    return path


def pad_to_page_multiple(path: Path, page_size: int = 4096) -> Path:
    data = path.read_bytes()
    rem = len(data) % page_size
    if rem:
        path.write_bytes(data + b"\x00" * (page_size - rem))
    return path
