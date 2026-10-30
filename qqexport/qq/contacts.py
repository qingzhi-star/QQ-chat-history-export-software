"""T6：联系人/群资料解析（显示名优先级 + contacts.json 快照）。"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from . import msgmap as mm

SELF_NAME = "我"


def _ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)


@dataclass
class Friend:
    uid: str | None = None
    uin: str | None = None
    nick: str | None = None
    remark: str | None = None


@dataclass
class Member:
    uid: str | None = None
    uin: str | None = None
    nick: str | None = None
    card: str | None = None


def _finish_name(name: str, uin: str | None) -> str:
    if uin and not name.startswith("QQ号") and not name.endswith(f"（{uin}）"):
        return f"{name}（{uin}）"
    return name


@dataclass
class Contacts:
    account_uin: str = ""
    friends_by_uid: dict[str, Friend] = field(default_factory=dict)
    friends_by_uin: dict[str, Friend] = field(default_factory=dict)
    group_names: dict[str, str] = field(default_factory=dict)
    members_by_uid: dict[tuple[str, str], Member] = field(default_factory=dict)
    members_by_uin: dict[tuple[str, str], Member] = field(default_factory=dict)

    # ---- 加载 ----
    @classmethod
    def load(cls, profile_db: Path | None, group_db: Path | None, *,
             account_uin: str = "") -> "Contacts":
        c = cls(account_uin=account_uin)
        if profile_db is not None and profile_db.is_file():
            c._load_profile(profile_db)
        if group_db is not None and group_db.is_file():
            c._load_group(group_db)
        return c

    def _load_profile(self, path: Path) -> None:
        con = _ro(path)
        try:
            nick_by_uid: dict[str, Friend] = {}
            if _has_table(con, mm.PROFILE_TABLE):
                cols = _columns(con, mm.PROFILE_TABLE)
                wanted = [c for c in (mm.PROFILE_UID, mm.PROFILE_UIN,
                                      mm.PROFILE_NICK, mm.PROFILE_REMARK)
                          if c in cols]
                for row in con.execute(
                        f'SELECT {_qlist(wanted)} FROM "{mm.PROFILE_TABLE}"'):
                    data = dict(zip(wanted, row))
                    f = Friend(uid=_s(data.get(mm.PROFILE_UID)),
                               uin=_s(data.get(mm.PROFILE_UIN)),
                               nick=_s(data.get(mm.PROFILE_NICK)),
                               remark=_s(data.get(mm.PROFILE_REMARK)))
                    nick_by_uid[f.uid] = f
            if _has_table(con, mm.BUDDY_TABLE):
                cols = _columns(con, mm.BUDDY_TABLE)
                wanted = [c for c in (mm.BUDDY_UID, mm.BUDDY_UIN, mm.BUDDY_REMARK)
                          if c in cols]
                for row in con.execute(
                        f'SELECT {_qlist(wanted)} FROM "{mm.BUDDY_TABLE}"'):
                    data = dict(zip(wanted, row))
                    uid = _s(data.get(mm.BUDDY_UID))
                    base = nick_by_uid.get(uid) or Friend(uid=uid)
                    base.uin = _s(data.get(mm.BUDDY_UIN)) or base.uin
                    remark = _s(data.get(mm.BUDDY_REMARK))
                    if remark:
                        base.remark = remark
                    self._index_friend(base)
            for f in nick_by_uid.values():
                self._index_friend(f)
        finally:
            con.close()

    def _index_friend(self, f: Friend) -> None:
        if f.uid:
            self.friends_by_uid[f.uid] = f
        if f.uin:
            self.friends_by_uin[f.uin] = f

    def _load_group(self, path: Path) -> None:
        con = _ro(path)
        try:
            if _has_table(con, mm.GROUP_TABLE):
                cols = _columns(con, mm.GROUP_TABLE)
                wanted = [c for c in (mm.GROUP_ID, mm.GROUP_NAME) if c in cols]
                for row in con.execute(
                        f'SELECT {_qlist(wanted)} FROM "{mm.GROUP_TABLE}"'):
                    data = dict(zip(wanted, row))
                    gid = _s(data.get(mm.GROUP_ID))
                    name = _s(data.get(mm.GROUP_NAME))
                    if gid and name:
                        self.group_names[gid] = name
            if _has_table(con, mm.MEMBER_TABLE):
                cols = _columns(con, mm.MEMBER_TABLE)
                wanted = [c for c in (mm.MEMBER_GROUP, mm.MEMBER_UID,
                                      mm.MEMBER_UIN, mm.MEMBER_NICK,
                                      mm.MEMBER_CARD) if c in cols]
                for row in con.execute(
                        f'SELECT {_qlist(wanted)} FROM "{mm.MEMBER_TABLE}"'):
                    data = dict(zip(wanted, row))
                    group = _s(data.get(mm.MEMBER_GROUP))
                    if not group:
                        continue
                    m = Member(uid=_s(data.get(mm.MEMBER_UID)),
                               uin=_s(data.get(mm.MEMBER_UIN)),
                               nick=_s(data.get(mm.MEMBER_NICK)),
                               card=_s(data.get(mm.MEMBER_CARD)))
                    if m.uid:
                        self.members_by_uid[(group, m.uid)] = m
                    if m.uin:
                        self.members_by_uin[(group, m.uin)] = m
        finally:
            con.close()

    # ---- 查询 ----
    def group_name(self, group_id: str | None) -> str | None:
        return self.group_names.get(group_id) if group_id else None

    def uin_of(self, uid: str | None, uin: str | None,
               group_id: str | None = None) -> str | None:
        if group_id:
            member = (self.members_by_uid.get((group_id, uid)) if uid else None) \
                or (self.members_by_uin.get((group_id, uin)) if uin else None)
            if member and member.uin:
                return member.uin
        friend = (self.friends_by_uid.get(uid) if uid else None) \
            or (self.friends_by_uin.get(uin) if uin else None)
        if friend and friend.uin:
            return friend.uin
        return uin

    def display_name(self, uid: str | None, uin: str | None,
                     group_id: str | None = None) -> str:
        """资料昵称优先；群名片只作括号补充；最后附 QQ 号。"""
        member = None
        if group_id:
            member = (self.members_by_uid.get((group_id, uid)) if uid else None) \
                or (self.members_by_uin.get((group_id, uin)) if uin else None)
        friend = (self.friends_by_uid.get(uid) if uid else None) \
            or (self.friends_by_uin.get(uin) if uin else None)
        nick = (friend.nick if friend and friend.nick else
                member.nick if member and member.nick else
                friend.remark if friend and friend.remark else
                member.card if member and member.card else None)
        card = member.card if member and member.card else None
        if nick and card and card != nick:
            return _finish_name(f"{nick}（{card}）", uin)
        if nick:
            return _finish_name(nick, uin)
        if uin:
            return f"QQ号{uin}"
        if uid:
            return f"未知用户{uid[:8]}"
        return "未知用户"

    def snapshot(self, used: set[tuple[str | None, str | None, str | None]] | None = None
                 ) -> dict:
        """只导出本次用到的名字；键排序固定，便于确定性。"""
        records = []
        for group_id, uid, uin in sorted(used or set(),
                                         key=lambda x: (x[0] or "", x[1] or "", x[2] or "")):
            records.append({
                "group": group_id,
                "uid": uid,
                "uin": uin,
                "name": self.display_name(uid, uin, group_id),
            })
        return {"version": 1, "used": records}


def _columns(con: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in con.execute(f'PRAGMA table_info("{table}")')]


def _has_table(con: sqlite3.Connection, table: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,)).fetchone()
    return row is not None


def _qlist(cols: list[str]) -> str:
    return ", ".join(f'"{c}"' for c in cols)


def _s(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        return None
    text = str(value).strip()
    return text or None
