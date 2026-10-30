"""T11：ExportSource —— 解密主库/伴随库并在生命周期内提供解析数据。"""
from __future__ import annotations

import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path

from qqexport.core import companions, decrypt, discover
from qqexport.core import sqlcipher as sc
from qqexport.qq import contacts, media, messages, tables


class ExportError(RuntimeError):
    pass


@dataclass(frozen=True)
class ChatInfo:
    chat_id: str
    kind: str
    peer: str
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
                       meta={"account": account, "decrypt": result})
        except Exception:
            tmp.cleanup()
            raise

    @classmethod
    def from_parts(cls, msg_db: Path, contacts_obj: contacts.Contacts,
                   media_index: media.MediaIndex, account_uin: str) -> "ExportSource":
        return cls(msg_db, contacts_obj, media_index, account_uin)

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
        finally:
            con.close()
        out = []
        for s in summaries:
            if s.kind == tables.KIND_GROUP:
                title = self.contacts.group_name(s.peer) or f"群{s.peer}"
                subtitle = f"群号 {s.peer}"
            else:
                title = self.contacts.display_name(s.peer, None)
                subtitle = None
            out.append(ChatInfo(s.chat_id, s.kind, s.peer, title, subtitle,
                                s.messages, s.first_ts, s.last_ts))
        return out

    def iter_messages(self, chat_id: str):
        kind, peer = chat_id.split(":", 1)
        con = sqlite3.connect(self.msg_db.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            yield from messages.iter_messages(con, kind, peer, self.ctx)
        finally:
            con.close()
