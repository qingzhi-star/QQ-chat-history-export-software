"""显式导入的本机明文资料库；按账号隔离、事务合并、不回写 QQ。"""
from dataclasses import asdict,replace
import json
from pathlib import Path
import shutil
import sqlite3
import threading

from qqexport import config
from qqexport.errors import UserError
from qqexport.export.cancel import check_cancel
from qqexport.export.serde import MediaSnapshot,message_data,from_data,logical_hash,hash_file,copy_file
from qqexport.export.session import ChatInfo
from qqexport.filelock import FileLease
from qqexport.temporary import workspace


class LibraryError(UserError):
    pass


class Library:
    def __init__(self):
        self.root = config.app_dir()/"library"
        self.root.mkdir(parents=True,exist_ok=True)
        self.path = self.root/"library.sqlite3"
        self.lease = FileLease(self.root/".usage.lock")
        self.closed,self.lock = False,threading.RLock()
        try:
            self.con = sqlite3.connect(self.path,check_same_thread=False)
            if self.con.execute("PRAGMA user_version").fetchone()[0] not in {0,1}:
                raise LibraryError("资料库版本较新，请使用匹配的软件版本；原文件保留")
            self.con.execute("PRAGMA journal_mode=WAL")
            self.con.execute("CREATE TABLE IF NOT EXISTS accounts(owner TEXT PRIMARY KEY,label TEXT)")
            self.con.execute("CREATE TABLE IF NOT EXISTS chats(owner TEXT,chat TEXT,info TEXT,PRIMARY KEY(owner,chat))")
            self.con.execute("CREATE TABLE IF NOT EXISTS messages(owner TEXT,chat TEXT,ts INTEGER,mid INTEGER,"
                             "body TEXT,digest TEXT,PRIMARY KEY(owner,chat,ts,mid)) WITHOUT ROWID")
            for table,expected in {"accounts":["owner","label"],"chats":["owner","chat","info"],
                                   "messages":["owner","chat","ts","mid","body","digest"]}.items():
                if [row[1] for row in self.con.execute(f"PRAGMA table_info({table})")]!=expected:
                    raise LibraryError("资料库结构不匹配，请保留原文件后使用其他目录")
            self.con.execute("PRAGMA user_version=1")
            self.con.commit()
        except BaseException:
            if hasattr(self,"con"):
                self.con.close()
            self.lease.close()
            raise

    def close(self):
        if not self.closed:
            self.con.close()
            self.lease.close()
            self.closed = True

    def accounts(self):
        with self.lock:
            return [{"owner":owner,"label":label,"messages":count} for owner,label,count in self.con.execute(
                "SELECT a.owner,a.label,COUNT(m.mid) FROM accounts a LEFT JOIN messages m ON m.owner=a.owner GROUP BY a.owner ORDER BY a.owner")]

    def import_archive(self,reader,*,cancel=None):
        owner = reader.manifest["account_sha256"]
        added,duplicates = 0,0
        installed = []
        with self.lock,workspace("archive-build") as temp:
            stage = Path(temp)
            copier = MediaSnapshot(stage,cancel)
            self.con.execute("BEGIN IMMEDIATE")
            try:
                self.con.execute("INSERT OR IGNORE INTO accounts VALUES (?,?)",(owner,reader.manifest.get("account_label",owner[:12])))
                source = reader.source()
                for info in source.chats():
                    for message in source.iter_messages(info.chat_id):
                        check_cancel(cancel)
                        digest = logical_hash(message)
                        existing = self.con.execute("SELECT digest FROM messages WHERE owner=? AND chat=? AND ts=? AND mid=?",
                            (owner,info.chat_id,message.ts,message.msg_id)).fetchone()
                        if existing:
                            if existing[0]!=digest:
                                raise LibraryError("同一消息标识存在不同内容，已取消合并；请保留原归档另行核对")
                            duplicates += 1
                            continue
                        copied = copier.message(message)
                        self.con.execute("INSERT INTO messages VALUES (?,?,?,?,?,?)",(owner,info.chat_id,message.ts,
                            message.msg_id,json.dumps(message_data(copied),ensure_ascii=False,separators=(",",":")),digest))
                        added += 1
                    total,first,last = self.con.execute("SELECT COUNT(*),MIN(ts),MAX(ts) FROM messages WHERE owner=? AND chat=?",
                                                       (owner,info.chat_id)).fetchone()
                    combined = replace(info,messages=total,first_ts=first,last_ts=last)
                    self.con.execute("INSERT OR REPLACE INTO chats VALUES (?,?,?)",(owner,info.chat_id,json.dumps(asdict(combined),ensure_ascii=False)))
                media = stage/"media"
                if media.exists():
                    for path in media.iterdir():
                        check_cancel(cancel)
                        target = self.root/"media"/path.name
                        target.parent.mkdir(exist_ok=True)
                        if target.exists():
                            if hash_file(target,cancel)!=hash_file(path,cancel):
                                raise LibraryError("资料库媒体已被修改，合并已停止")
                        else:
                            copy_file(path,target,cancel)
                            installed.append(target)
                check_cancel(cancel)
                self.con.commit()
            except BaseException:
                # 仍持有写事务时删除新文件，避免其他导入先引用这些文件。
                for path in installed:
                    path.unlink(missing_ok=True)
                self.con.rollback()
                raise
        return {"owner":owner,"added":added,"duplicates":duplicates}

    def source(self,owner):
        # 每个视图拥有独立连接和长读事务，其他进程导入不会移动当前选择的索引。
        owned = Library()
        owned.con.execute("BEGIN")
        if owned.con.execute("SELECT 1 FROM accounts WHERE owner=?",(owner,)).fetchone() is None:
            owned.close()
            raise LibraryError("未找到该账号的资料")
        return LibrarySource(owned,owner)

    @staticmethod
    def clear():
        root = config.app_dir()/"library"
        if not root.exists():
            return
        if root.is_symlink() or getattr(root,"is_junction",lambda:False)():
            raise LibraryError("资料库目录为链接，拒绝自动清理")
        with FileLease(root/".usage.lock",exclusive=True):
            for path in root.iterdir():
                if path.name==".usage.lock":
                    continue
                if path.is_symlink() or getattr(path,"is_junction",lambda:False)():
                    path.unlink()
                elif path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()


class LibrarySource:
    def __init__(self,library,owner):
        self.library,self.account_uin = library,owner
        self.msg_db,self.warnings = library.path,[]
        self.meta = {"synthetic":True,"library":True,"account_sha256":owner,
                     "display_account":"本机资料库","protected_dirs":[library.root]}

    def chats(self):
        with self.library.lock:
            return [ChatInfo(**json.loads(row[0])) for row in self.library.con.execute(
                "SELECT info FROM chats WHERE owner=? ORDER BY chat",(self.account_uin,))]

    def count(self,chat):
        with self.library.lock:
            return self.library.con.execute("SELECT COUNT(*) FROM messages WHERE owner=? AND chat=?",(self.account_uin,chat)).fetchone()[0]

    def _message(self,body,digest):
        message = from_data(json.loads(body),self.library.root)
        if logical_hash(message)!=digest:
            raise LibraryError("资料库消息校验失败")
        return message

    def window(self,chat,offset,limit=128):
        if offset<0 or not 1<=limit<=1000:
            raise LibraryError("资料分页参数无效")
        with self.library.lock:
            rows = self.library.con.execute("SELECT body,digest FROM messages WHERE owner=? AND chat=? ORDER BY ts,mid LIMIT ? OFFSET ?",
                                           (self.account_uin,chat,limit,offset)).fetchall()
        return [(offset+i,self._message(*row)) for i,row in enumerate(rows)]

    def iter_messages(self,chat):
        for offset in range(0,self.count(chat),128):
            yield from (m for _,m in self.window(chat,offset))

    def indices_in_range(self,chat,start=None,end=None):
        with self.library.lock:
            rows = self.library.con.execute("SELECT ts FROM messages WHERE owner=? AND chat=? ORDER BY ts,mid",(self.account_uin,chat))
            return {i for i,(ts,) in enumerate(rows) if (start is None or ts>=start) and (end is None or ts<=end)}

    def ids_for_indices(self,chat,selected):
        with self.library.lock:
            rows = self.library.con.execute("SELECT mid FROM messages WHERE owner=? AND chat=? ORDER BY ts,mid",(self.account_uin,chat))
            return {mid for i,(mid,) in enumerate(rows) if i in selected}

    def close(self):
        self.library.close()
    retire = close
