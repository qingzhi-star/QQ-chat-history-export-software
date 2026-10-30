"""T3：表/列发现与流式读取（P1）。"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from typing import Iterator

from .msgmap import COL_MSG_ID, COL_PEER_ID, COL_TIMESTAMP, MSG_COLUMNS

KIND_C2C = "c2c"
KIND_GROUP = "group"

_C2C_RE = re.compile(r"^c2c_msg_table")
_GROUP_RE = re.compile(r"^group_msg_table")


@dataclass(frozen=True)
class TableSet:
    c2c: str | None
    group: str | None


@dataclass(frozen=True)
class ChatSummary:
    kind: str
    peer: str
    messages: int
    first_ts: int | None
    last_ts: int | None

    @property
    def chat_id(self) -> str:
        return f"{self.kind}:{self.peer}"


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _table_names(con: sqlite3.Connection) -> list[str]:
    return [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")]


def discover(con: sqlite3.Connection) -> TableSet:
    names = _table_names(con)
    c2c = next((n for n in names if _C2C_RE.match(n)), None)
    group = next((n for n in names if _GROUP_RE.match(n)), None)
    return TableSet(c2c=c2c, group=group)


def columns_of(con: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in con.execute(f"PRAGMA table_info({_quote(table)})")]


def select_columns(con: sqlite3.Connection, table: str,
                   wanted: tuple[str, ...] = MSG_COLUMNS) -> list[str]:
    have = set(columns_of(con, table))
    return [c for c in wanted if c in have]


def iter_rows(con: sqlite3.Connection, table: str, *,
              columns: tuple[str, ...] = MSG_COLUMNS,
              batch: int = 1000) -> Iterator[dict[str, object]]:
    if batch < 1:
        raise ValueError("batch must be >= 1")
    have = set(columns_of(con, table))
    selected = [c for c in columns if c in have]
    sql = (f"SELECT {', '.join(_quote(c) for c in selected)} "
           f"FROM {_quote(table)}")
    cursor = con.execute(sql)
    while True:
        page = cursor.fetchmany(batch)
        if not page:
            return
        for values in page:
            row: dict[str, object] = {c: None for c in columns}
            row.update(zip(selected, values))
            yield row


def iter_chat_rows(con: sqlite3.Connection, kind: str, peer: str, *,
                   batch: int = 1000) -> Iterator[dict[str, object]]:
    table = discover(con)
    name = table.c2c if kind == KIND_C2C else table.group
    if name is None or COL_PEER_ID not in columns_of(con, name):
        return
    sql = (f'SELECT {", ".join(_quote(c) for c in MSG_COLUMNS if c in columns_of(con, name))} '
           f'FROM {_quote(name)} WHERE {_quote(COL_PEER_ID)} = ? '
           f'ORDER BY {_quote(COL_TIMESTAMP)} ASC, {_quote(COL_MSG_ID)} ASC')
    cursor = con.execute(sql, (peer,))
    while True:
        page = cursor.fetchmany(batch)
        if not page:
            return
        for values in page:
            row: dict[str, object] = {c: None for c in MSG_COLUMNS}
            row.update(zip([c for c in MSG_COLUMNS if c in columns_of(con, name)], values))
            yield row


def list_chats(con: sqlite3.Connection) -> list[ChatSummary]:
    table = discover(con)
    out: list[ChatSummary] = []
    for kind, name in ((KIND_C2C, table.c2c), (KIND_GROUP, table.group)):
        if name is None or COL_PEER_ID not in columns_of(con, name):
            continue
        sql = (f'SELECT {_quote(COL_PEER_ID)}, COUNT(*), '
               f'MIN({_quote(COL_TIMESTAMP)}), MAX({_quote(COL_TIMESTAMP)}) '
               f'FROM {_quote(name)} WHERE {_quote(COL_PEER_ID)} IS NOT NULL '
               f'GROUP BY {_quote(COL_PEER_ID)}')
        for peer, count, first_ts, last_ts in con.execute(sql):
            out.append(ChatSummary(kind=kind, peer=str(peer),
                                   messages=int(count),
                                   first_ts=int(first_ts) if first_ts is not None else None,
                                   last_ts=int(last_ts) if last_ts is not None else None))
    return sorted(out, key=lambda c: c.chat_id)
