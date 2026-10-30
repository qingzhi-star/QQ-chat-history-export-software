"""T11：ExportSource —— 解密主库/伴随库并在生命周期内提供解析数据。"""
from __future__ import annotations

import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path

from qqexport.core import companions, decrypt, discover
from qqexport.core import sqlcipher as sc
from qqexport.qq import contacts, media, messages, tables
from qqexport.qq import msgmap as mm


class ExportError(RuntimeError):
    pass


@dataclass(frozen=True)
class ChatInfo:
    chat_id: str
    kind: str
    peer: str
    account: str
    title: str
    subtitle: str | None
    messages: int
    first_ts: int | None
    last_ts: int | None


class ExportSource:
    def __init__(self, msg_db: Path, contacts_obj: contacts.Contacts,
                 media_index: media.MediaIndex, account_uin: str, *,
                 warnings: list[str] | None = None,
                 tmp: tempfile.TemporaryDirectory | None = None,
                 meta: dict | None = None):
        self.msg_db = msg_db
        self.contacts = contacts_obj
        self.media_index = media_index
        self.account_uin = account_uin
        self.warnings = warnings or []
        self._tmp = tmp
        self.meta = meta or {}
        self.ctx = messages.ParseContext(
            account_uin=account_uin,
            display_name=contacts_obj.display_name,
            resolve_media=lambda kind, md5, local, name:
                media.resolve(media_index, kind, md5, local, name))

    @classmethod
    def open(cls, account: discover.QQAccount, passphrase: bytes, *,
             work_dir: Path | None = None) -> "ExportSource":
        tmp = tempfile.TemporaryDirectory(prefix="qqexport-export-",
                                          dir=str(work_dir) if work_dir else None)
        root = Path(tmp.name)
        warnings: list[str] = []
        try:
            result = decrypt.decrypt_database(account, passphrase, dst_dir=root / "msg")
            if result.status == "failed" or not result.plaintext_db.is_file():
                raise ExportError("主库解密失败")
            profile = group = None
            try:
                profile = companions.decrypt_companion(
                    account.db_dir / "profile_info.db", passphrase, root / "comp")
            except companions.CompanionError:
                warnings.append("profile_info.db 解密失败，显示名回退")
            try:
                group = companions.decrypt_companion(
                    account.db_dir / "group_info.db", passphrase, root / "comp")
            except companions.CompanionError:
                warnings.append("group_info.db 解密失败，群名/名片回退")
            contacts_obj = contacts.Contacts.load(profile, group,
                                                  account_uin=account.uin)
            index = media.build_index(account.media_dir)
            return cls(result.plaintext_db, contacts_obj, index, account.uin,
                       warnings=warnings, tmp=tmp,
                       meta={"account": account, "decrypt": result,
                             "nt_data": str(account.db_dir.parent / "nt_data")})
        except Exception:
            tmp.cleanup()
            raise

    @classmethod
    def from_parts(cls, msg_db: Path, contacts_obj: contacts.Contacts,
                   media_index: media.MediaIndex, account_uin: str) -> "ExportSource":
        return cls(msg_db, contacts_obj, media_index, account_uin,
                   meta={"synthetic": True})

    def __enter__(self) -> "ExportSource":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        if self._tmp is not None:
            self._tmp.cleanup()
            self._tmp = None

    def chats(self) -> list[ChatInfo]:
        con = sqlite3.connect(self.msg_db.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            summaries = tables.list_chats(con)
            table_set = tables.discover(con)
            out = []
            for s in summaries:
                if s.kind == tables.KIND_GROUP:
                    title = self.contacts.group_name(s.peer) or f"群{s.peer}"
                    subtitle = f"群号 {s.peer}"
                    account = s.peer
                else:
                    # 只有 40011=1 系统事件、没有正常消息的会话不展示
                    if table_set.c2c is not None:
                        row = con.execute(
                            f'SELECT COUNT(*), '
                            f'SUM(CASE WHEN COALESCE("{mm.COL_OUTER_TYPE}", 0) IN (1) '
                            f'THEN 0 ELSE 1 END) '
                            f'FROM "{table_set.c2c}" WHERE "{mm.COL_PEER_ID}" = ?',
                            (s.peer,)).fetchone()
                        if row is None or not row[1]:
                            continue
                    account = self._peer_uin(s.kind, s.peer) or s.peer
                    title = self.contacts.display_name(s.peer, account)
                    subtitle = None
                out.append(ChatInfo(s.chat_id, s.kind, s.peer, account, title,
                                    subtitle, s.messages, s.first_ts, s.last_ts))
            return out
        finally:
            con.close()

    def _peer_uin(self, kind: str, peer: str) -> str | None:
        con = sqlite3.connect(self.msg_db.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            table = tables.discover(con)
            name = table.c2c if kind == tables.KIND_C2C else table.group
            if name is None:
                return None
            row = con.execute(
                f'SELECT "{mm.COL_PEER_UIN}" FROM "{name}" '
                f'WHERE "{mm.COL_PEER_ID}" = ? AND "{mm.COL_PEER_UIN}" IS NOT NULL '
                f'LIMIT 1', (peer,)).fetchone()
            return str(row[0]) if row and row[0] else None
        finally:
            con.close()

    def iter_messages(self, chat_id: str):
        kind, peer = chat_id.split(":", 1)
        ctx = messages.ParseContext(
            account_uin=self.ctx.account_uin,
            kind=kind,
            group_id=peer if kind == tables.KIND_GROUP else None,
            display_name=self.ctx.display_name,
            resolve_media=self.ctx.resolve_media,
            resolve_uin=self.contacts.uin_of)
        con = sqlite3.connect(self.msg_db.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            yield from messages.iter_messages(con, kind, peer, ctx)
        finally:
            con.close()
