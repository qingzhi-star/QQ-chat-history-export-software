"""会话/全库内存搜索；FTS5 trigram 可用时加速，否则有界分页扫描。"""
from __future__ import annotations
from dataclasses import dataclass
import sqlite3
import threading
import time

import regex
from qqexport.export.txt import segment_text


class SearchError(ValueError):
    pass


@dataclass(frozen=True)
class Query:
    text: str = ""
    chat_id: str | None = None
    sender: str | None = None
    kind: str | None = None
    start: int | None = None
    end: int | None = None
    mode: str = "contains"


@dataclass(frozen=True)
class Hit:
    chat_id: str
    msg_id: int
    ts: int
    sender: str
    summary: str
    position: int


class SearchIndex:
    def __init__(self, *, fts=True):
        self.lock = threading.RLock()
        self.con = sqlite3.connect(":memory:", check_same_thread=False)
        self.con.execute("CREATE TABLE docs (chat TEXT, mid INTEGER, ts INTEGER, sender TEXT, "
                         "uid TEXT, uin TEXT, kinds TEXT, body TEXT, folded TEXT, position INTEGER)")
        self.con.execute("CREATE INDEX filters ON docs(chat,ts,mid)")
        self.con.execute("CREATE INDEX global_order ON docs(ts,chat,mid,position)")
        self.con.execute("CREATE INDEX exact_body ON docs(folded)")
        self.fts = False
        if fts:
            try:
                self.con.execute("CREATE VIRTUAL TABLE words USING fts5(folded,content='docs',"
                                 "content_rowid='rowid',tokenize='trigram')")
                self.fts = True
            except sqlite3.OperationalError:
                pass
        self.ready = False
        self.count = 0

    def close(self):
        with self.lock:
            self.con.close()

    def clear(self):
        with self.lock:
            if self.fts:
                self.con.execute("INSERT INTO words(words) VALUES('delete-all')")
            self.con.execute("DELETE FROM docs")
            self.con.commit()
            self.ready, self.count = False, 0

    def build(self, repo, *, cancel=None, progress=None):
        self.clear()
        with self.lock:
            try:
                for info in repo.chats():
                    for offset in range(0,repo.count(info.chat_id),512):
                        if cancel is not None and cancel.is_set():
                            raise SearchError("已取消索引")
                        batch = []
                        for position,msg in repo.window(info.chat_id,offset,512):
                            body = "\n".join(segment_text(s) for s in msg.segments)
                            kinds = "|"+"|".join(sorted({s.kind for s in msg.segments}))+"|"
                            batch.append((info.chat_id,msg.msg_id,msg.ts,msg.sender_name,msg.sender_uid,
                                          msg.sender_uin,kinds,body,body.casefold(),position))
                        self.con.executemany("INSERT INTO docs VALUES (?,?,?,?,?,?,?,?,?,?)",batch)
                        self.count += len(batch)
                        if progress:
                            progress(self.count)
                if self.fts:
                    self.con.execute("INSERT INTO words(words) VALUES('rebuild')")
                self.con.commit()
                self.ready = True
            except BaseException:
                self.con.rollback()
                self.count = 0
                raise

    def find(self, query: Query, *, limit=200, offset=0, timeout=5, after=None):
        if not 1<=limit<=1000 or offset<0 or len(query.text)>1000:
            raise SearchError("搜索参数超出范围")
        if query.mode not in {"contains","exact","regex"}:
            raise SearchError("未知匹配方式")
        if query.start is not None and query.end is not None and query.start>query.end:
            raise SearchError("开始日期不能晚于结束日期")
        clauses,params = [],[]
        if after is not None:
            clauses.append("(d.ts,d.chat,d.mid,d.position)>(?,?,?,?)")
            params.extend(after)
        source = "docs d"
        text = query.text.casefold()
        if query.chat_id:
            clauses.append("d.chat=?")
            params.append(query.chat_id)
        if query.sender:
            clauses.append("(d.sender=? OR d.uid=? OR d.uin=?)")
            params.extend([query.sender]*3)
        if query.kind:
            clauses.append("instr(d.kinds,?)>0")
            params.append("|"+query.kind+"|")
        for value,op in ((query.start,">="),(query.end,"<=")):
            if value is not None:
                clauses.append("d.ts"+op+"?")
                params.append(value)
        if query.mode == "regex":
            try:
                pattern = regex.compile(query.text,regex.IGNORECASE)
            except regex.error:
                raise SearchError("正则表达式无效") from None
            deadline = time.monotonic()+timeout
            def matches(body):
                remaining = deadline-time.monotonic()
                if remaining<=0:
                    raise TimeoutError
                return bool(pattern.search(body,timeout=min(.02,remaining)))
            clauses.append("matches(d.body)")
        elif query.mode == "exact":
            clauses.append("d.folded=?")
            params.append(text)
        elif text:
            if self.fts and len(text)>=3:
                source += " JOIN words ON words.rowid=d.rowid"
                clauses.append("words MATCH ?")
                params.append('"'+text.replace('"','""')+'"')
            clauses.append("instr(d.folded,?)>0")
            params.append(text)
        where = " AND ".join(clauses) or "1"
        with self.lock:
            if not self.ready:
                raise SearchError("请先建立内存索引")
            if query.mode == "regex":
                self.con.create_function("matches",1,matches)
            try:
                rows = self.con.execute(
                    "SELECT d.chat,d.mid,d.ts,d.sender,substr(d.body,1,180),d.position FROM "+source+
                    " WHERE "+where+" ORDER BY d.ts,d.chat,d.mid,d.position LIMIT ? OFFSET ?",
                    (*params,limit,offset)).fetchall()
            except sqlite3.OperationalError:
                raise SearchError("搜索超时或索引不可用，请简化表达式") from None
        return [Hit(*row) for row in rows]

    def iter_hits(self, query):
        cursor = None
        with self.lock:
            while True:
                hits = self.find(query,limit=1000,after=cursor)
                yield from hits
                if len(hits)<1000:
                    return
                last = hits[-1]
                cursor = (last.ts,last.chat_id,last.msg_id,last.position)


class ResultSource:
    def __init__(self, repo, hits):
        self.repo = repo
        self.msg_db,self.account_uin,self.meta,self.warnings = repo.msg_db,repo.account_uin,repo.meta,repo.warnings
        self.positions = {}
        for hit in hits:
            self.positions.setdefault(hit.chat_id,set()).add(hit.position)

    def chats(self):
        return [info for info in self.repo.chats() if info.chat_id in self.positions]

    def iter_messages(self, chat):
        wanted = self.positions.get(chat,set())
        pages = sorted({index//128*128 for index in wanted})
        for offset in pages:
            for position,message in self.repo.window(chat,offset,128):
                if position in wanted:
                    yield message
