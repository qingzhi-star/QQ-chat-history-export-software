"""T6：显示名优先级、双轨索引与 contacts 快照。"""
from __future__ import annotations

from qqexport.qq import contacts
from tests.helpers import synth_db as sd


def make_profile(tmp_path):
    db = sd.create_companion_db(
        tmp_path / "profile_info.db", kind="profile",
        rows={
            "profile_info_v6": [
                {"1000": "u_a", "1002": 123456789, "20002": "昵称A", "20009": "备注A"},
                {"1000": "u_b", "1002": 987654321, "20002": "昵称B", "20009": None},
            ],
            "buddy_list": [
                {"1000": "u_a", "1001": None, "1002": 123456789},
                {"1000": "u_b", "1001": "备注B", "1002": 987654321},
            ],
        })
    return db


def make_group(tmp_path):
    return sd.create_companion_db(
        tmp_path / "group_info.db", kind="group",
        rows={
            "group_list": [{"60001": 111, "60007": "群名"}],
            "group_member3": [
                {"60001": 111, "1000": "u_a", "1002": 123456789,
                 "20002": "群昵称A", "64003": "群名片A"},
                {"60001": 111, "1000": "u_b", "1002": 987654321,
                 "20002": "群昵称B", "64003": None},
            ],
        })


def loaded(tmp_path, account_uin="123456789"):
    return contacts.Contacts.load(make_profile(tmp_path), make_group(tmp_path),
                                  account_uin=account_uin)


def test_group_nick_with_card(tmp_path):
    c = loaded(tmp_path)
    assert c.display_name("u_a", None, "111") == "昵称A（群名片A）"
    assert c.display_name("u_b", None, "111") == "昵称B"


def test_friend_nick_beats_remark_and_dual_track(tmp_path):
    c = loaded(tmp_path)
    assert c.display_name("u_a", None) == "昵称A"
    assert c.display_name("u_b", None) == "昵称B"
    assert c.display_name(None, "987654321") == "昵称B（987654321）"


def test_card_and_remark_are_only_fallbacks(tmp_path):
    db = sd.create_companion_db(
        tmp_path / "p.db", kind="profile",
        rows={"profile_info_v6": [{"1000": "u_a", "1002": 123456789,
                                   "20002": None, "20009": "备注A"}]})
    c = contacts.Contacts.load(db, None, account_uin="123456789")
    assert c.display_name("u_a", None) == "备注A"


def test_self_uin_uses_own_nickname(tmp_path):
    c = loaded(tmp_path)
    assert c.display_name(None, "123456789") == "昵称A（123456789）"


def test_fallbacks_for_unknown(tmp_path):
    c = loaded(tmp_path)
    assert c.display_name(None, "55555555") == "QQ号55555555"
    assert c.display_name("u_unknown", None) == "未知用户u_unknow"
    assert c.display_name(None, None) == "未知用户"


def test_missing_companions_fall_back(tmp_path):
    c = contacts.Contacts.load(None, None, account_uin="123456789")
    assert c.display_name("u_a", None) == "未知用户u_a"
    assert c.display_name(None, "55555555") == "QQ号55555555"
    assert c.group_name("111") is None


def test_snapshot_is_deterministic_and_filtered(tmp_path):
    c = loaded(tmp_path)
    used = {("111", "u_a", None), (None, "u_b", None)}
    snap = c.snapshot(used)
    assert snap["version"] == 1
    assert [r["name"] for r in snap["used"]] == ["昵称B", "昵称A（群名片A）"]
    assert snap == c.snapshot(set(reversed(list(used))))


def test_group_name(tmp_path):
    c = loaded(tmp_path)
    assert c.group_name("111") == "群名"
