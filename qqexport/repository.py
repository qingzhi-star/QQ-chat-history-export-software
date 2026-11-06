"""P2：只读源连接、内存游标索引与有界分页。正文不落独立索引文件。"""
from __future__ import annotations

from dataclasses import dataclass, replace
import sqlite3
import threading

from qqexport.qq import messages, tables, msgmap as mm


@dataclass(frozen=True, order=True)
class Cursor:
    ts: int
    msg_id: int
    row_id: int = 0


@dataclass(frozen=True)
class Page:
    messages: tuple
    cursor: Cursor | None
    exhausted: bool


class MessageRepository:
    """生命周期与解密副本一致。TEMP 内存表仅存定位键，不写 QQ 或正文。"""
    def __init__(self, source):
        self.source = source
        self.msg_db = source.msg_db
        self.account_uin, self.meta, self.warnings = source.account_uin, source.meta, source.warnings
        self._lock = threading.RLock()
        self._con = sqlite3.connect(self.msg_db.resolve().as_uri() + "?mode=ro", uri=True,
                                    check_same_thread=False)
        self._con.execute("PRAGMA temp_store=MEMORY")
        found = tables.discover(self._con)
        self._tables = {"c2c": found.c2c, "group": found.group}
        self._columns = {}
        try:
            self._con.execute("CREATE TEMP TABLE ordering (chat TEXT, ts INTEGER, mid INTEGER, "
                              "rid INTEGER, ord INTEGER, PRIMARY KEY(chat, ord)) WITHOUT ROWID")
            for kind, table in self._tables.items():
                if table is None:
                    continue
                columns = tables.columns_of(self._con, table)
                self._columns[kind] = columns
                if not {mm.COL_PEER_ID, mm.COL_TIMESTAMP, mm.COL_MSG_ID}.issubset(columns):
                    self._tables[kind] = None
                    continue
                peer, ts, mid = map(tables._quote, (mm.COL_PEER_ID, mm.COL_TIMESTAMP, mm.COL_MSG_ID))
                system = (f'COALESCE({tables._quote(mm.COL_OUTER_TYPE)},0) != 1'
                          if mm.COL_OUTER_TYPE in columns else "1")
                self._con.execute(
                    f"INSERT INTO ordering SELECT ? || {peer}, COALESCE({ts},0), COALESCE({mid},0), "
                    f"rowid, ROW_NUMBER() OVER(PARTITION BY {peer} ORDER BY COALESCE({ts},0),COALESCE({mid},0),rowid)-1 "
                    f"FROM {tables._quote(table)} WHERE {peer} IS NOT NULL AND {system}", (kind + ":",))
            self._con.execute("CREATE INDEX temp.cursor_order ON ordering(chat,ts,mid,rid)")
        except BaseException:
            self._con.close()
            raise

    def close(self):
        with self._lock:
            self._con.close()

    def chats(self):
        return self.source.chats()

    def count(self, chat_id):
        with self._lock:
            return self._con.execute("SELECT COUNT(*) FROM ordering WHERE chat=?", (chat_id,)).fetchone()[0]

    def _read(self, chat_id, where, params, limit, reverse=False, cursor_order=False):
        if not 1 <= limit <= 1000:
            raise ValueError("页大小必须在 1–1000 之间")
        kind, peer = chat_id.split(":", 1)
        table = self._tables.get(kind)
        if table is None:
            return []
        cols = self._columns[kind]
        selected = ",".join("m." + tables._quote(c) for c in cols)
        order = "DESC" if reverse else "ASC"
        ordering = ",".join(f"o.{key} {order}" for key in ("ts","mid","rid")) if cursor_order else f"o.ord {order}"
        with self._lock:
            rows = self._con.execute(
                f"SELECT o.ord,o.ts,o.mid,o.rid,{selected} FROM ordering o "
                f"JOIN {tables._quote(table)} m ON m.rowid=o.rid "
                f"WHERE o.chat=? {where} ORDER BY {ordering} LIMIT ?",
                (chat_id, *params, limit)).fetchall()
        ctx = replace(self.source.ctx, kind=kind, group_id=peer if kind == "group" else None,
                      resolve_uin=self.source.contacts.uin_of)
        return [(r[0], Cursor(*r[1:4]), messages.parse_message(dict(zip(cols, r[4:])), ctx)) for r in rows]

    def page(self, chat_id, *, after: Cursor | None = None, limit=128, reverse=False):
        where, params = "", ()
        if after is not None:
            op = "<" if reverse else ">"
            where = f"AND (o.ts,o.mid,o.rid) {op} (?,?,?)"
            params = (after.ts, after.msg_id, after.row_id)
        rows = self._read(chat_id, where, params, limit, reverse,cursor_order=True)
        output = tuple(r[2] for r in rows if not r[2].is_system)
        if reverse:
            output = tuple(reversed(output))
        return Page(output, rows[-1][1] if rows else after, len(rows) < limit)

    def window(self, chat_id, offset, limit=128):
        if offset < 0:
            raise ValueError("消息位置不能为负")
        return [(r[0], r[2]) for r in self._read(chat_id, "AND o.ord>=?", (offset,), limit)
                if not r[2].is_system]

    def iter_messages(self, chat_id, *, after=None):
        cursor = after
        while True:
            page = self.page(chat_id, after=cursor)
            yield from page.messages
            if page.exhausted:
                return
            cursor = page.cursor

    def position(self, chat_id, msg_id):
        with self._lock:
            row = self._con.execute("SELECT ord FROM ordering WHERE chat=? AND mid=? ORDER BY ord LIMIT 1",
                                    (chat_id, msg_id)).fetchone()
            return row[0] if row else None

    def indices_in_range(self, chat_id, start=None, end=None):
        clauses, params = ["chat=?"], [chat_id]
        if start is not None:
            clauses.append("ts>=?")
            params.append(start)
        if end is not None:
            clauses.append("ts<=?")
            params.append(end)
        with self._lock:
            return {r[0] for r in self._con.execute("SELECT ord FROM ordering WHERE " + " AND ".join(clauses), params)}

    def ids_for_indices(self, chat_id, selected):
        indices = sorted(selected)
        result = set()
        with self._lock:
            for offset in range(0, len(indices), 500):
                batch = indices[offset:offset+500]
                marks = ",".join("?" for _ in batch)
                result.update(r[0] for r in self._con.execute(
                    f"SELECT mid FROM ordering WHERE chat=? AND ord IN ({marks})", (chat_id, *batch)))
        return result

    def prefix_info(self,chat_id,ts,msg_id):
        with self._lock:
            count = self._con.execute("SELECT COUNT(*) FROM ordering WHERE chat=? AND (ts,mid)<=(?,?)",
                                     (chat_id,ts,msg_id)).fetchone()[0]
            first = self._con.execute("SELECT ts,mid FROM ordering WHERE chat=? ORDER BY ord LIMIT 1",
                                     (chat_id,)).fetchone()
            position = self._con.execute("SELECT ord FROM ordering WHERE chat=? AND ts=? AND mid=? ORDER BY ord DESC LIMIT 1",
                                        (chat_id,ts,msg_id)).fetchone()
        items = self.window(chat_id,position[0],1) if position is not None else []
        message = items[0][1] if items else None
        return count,list(first) if first else None,message
