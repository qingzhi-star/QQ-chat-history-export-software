"""P2：只读源连接、内存游标索引与有界分页。正文不落独立索引文件。"""
from __future__ import annotations

from dataclasses import dataclass, replace
from collections import OrderedDict
import hashlib
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
        self._pages = OrderedDict()
        self._page_bytes = self._page_rows = 0
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
            self._con.execute("CREATE TEMP TABLE summaries AS SELECT chat,COUNT(*) AS amount,"
                              "MIN(ts) AS first_ts,MAX(ts) AS last_ts,MAX(ord) AS last_ord "
                              "FROM ordering GROUP BY chat")
            self._con.execute("CREATE UNIQUE INDEX temp.summary_chat ON summaries(chat)")
            self._counts = dict(self._con.execute("SELECT chat,amount FROM summaries"))
            self._sidebar = None
        except BaseException:
            self._con.close()
            raise

    def close(self):
        with self._lock:
            self._pages.clear()
            self._page_bytes = self._page_rows = 0
            self._con.close()

    def chats(self):
        return self.sidebar()[0]

    def sidebar(self):
        """一次汇总会话及末条预览，避免每个联系人重新扫描整张消息表。"""
        from qqexport.export.session import ChatInfo
        from qqexport.export.txt import segment_text
        with self._lock:
            if self._sidebar is not None:
                infos,metas = self._sidebar
                return list(infos),dict(metas)
            infos,metas = [],{}
            self._versions = {}
            for kind,table in self._tables.items():
                if table is None:
                    continue
                cols = self._columns[kind]
                selected = ",".join("m."+tables._quote(c) for c in cols)
                rows = self._con.execute(
                    f"SELECT s.chat,s.amount,s.first_ts,s.last_ts,{selected} FROM summaries s "
                    "JOIN ordering o ON o.chat=s.chat AND o.ord=s.last_ord "
                    f"JOIN {tables._quote(table)} m ON m.rowid=o.rid "
                    "WHERE s.chat>=? AND s.chat<?",(kind+":",kind+";")).fetchall()
                peer_uins = {}
                if kind=="c2c" and mm.COL_PEER_UIN in cols:
                    missing = any(not row[4+cols.index(mm.COL_PEER_UIN)] for row in rows)
                    if missing:
                        peer_uins = dict(self._con.execute(
                            f"SELECT {tables._quote(mm.COL_PEER_ID)},MAX({tables._quote(mm.COL_PEER_UIN)}) "
                            f"FROM {tables._quote(table)} WHERE {tables._quote(mm.COL_PEER_UIN)} IS NOT NULL "
                            f"GROUP BY {tables._quote(mm.COL_PEER_ID)}"))
                for chat,amount,first_ts,last_ts,*values in rows:
                    peer = chat.split(":",1)[1]
                    raw = dict(zip(cols,values))
                    if kind=="group":
                        account = peer
                        title = self.source.contacts.group_name(peer) or f"群{peer}"
                        subtitle = f"群号 {peer}"
                    else:
                        account = raw.get(mm.COL_PEER_UIN) or peer_uins.get(peer)
                        account = self.source.contacts.uin_of(peer,str(account) if account else None) or peer
                        title = self.source.contacts.display_name(peer,account)
                        subtitle = None
                    ctx = replace(self.source.ctx,kind=kind,group_id=peer if kind=="group" else None,
                                  resolve_uin=self.source.contacts.uin_of)
                    last = messages.parse_message(raw,ctx)
                    if not last.is_system:
                        body = " ".join(segment_text(s) for s in last.segments).replace("\n"," ")
                        metas[chat] = (body[:48],last.ts)
                    blob = raw.get(mm.COL_BLOB)
                    digest = hashlib.sha256(blob if isinstance(blob,bytes) else b"").digest()
                    self._versions[chat] = (amount,last_ts,last.msg_id,digest,title)
                    infos.append(ChatInfo(chat,kind,peer,str(account),title,subtitle,amount,first_ts,last_ts))
            infos.sort(key=lambda info:(-metas.get(info.chat_id,("",info.last_ts))[1],info.chat_id))
            self._sidebar = infos,metas
            return list(infos),dict(metas)

    def chat_revision(self,chat_id):
        if self._sidebar is None:
            self.sidebar()
        return self._versions.get(chat_id)

    def count(self, chat_id):
        return self._counts.get(chat_id,0)

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
        key = (chat_id,where,tuple(params),limit,reverse,cursor_order)
        with self._lock:
            if key in self._pages:
                self._pages.move_to_end(key)
                return list(self._pages[key][0])
            rows = self._con.execute(
                f"SELECT o.ord,o.ts,o.mid,o.rid,{selected} FROM ordering o "
                f"JOIN {tables._quote(table)} m ON m.rowid=o.rid "
                f"WHERE o.chat=? {where} ORDER BY {ordering} LIMIT ?",
                (chat_id, *params, limit)).fetchall()
            ctx = replace(self.source.ctx, kind=kind, group_id=peer if kind == "group" else None,
                          resolve_uin=self.source.contacts.uin_of)
            output = [(r[0], Cursor(*r[1:4]), messages.parse_message(dict(zip(cols, r[4:])), ctx)) for r in rows]
            # 仅缓存当前只读副本的解析页；限制页数、行数和原始载荷，关闭即清理。
            weight = sum(len(value)*2 if isinstance(value,str) else len(value)
                         if isinstance(value,bytes) else 8 for row in rows for value in row)
            if weight<=8*1024*1024 and output:
                self._pages[key] = (tuple(output),weight)
                self._page_bytes += weight
                self._page_rows += len(output)
                while len(self._pages)>16 or self._page_rows>2048 or self._page_bytes>8*1024*1024:
                    old,size = self._pages.popitem(last=False)[1]
                    self._page_bytes -= size
                    self._page_rows -= len(old)
            return output

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
