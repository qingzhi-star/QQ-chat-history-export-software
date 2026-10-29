import os
from pathlib import Path

import pytest

from qqexport.core import discover


def make_account(root: Path, uin: str, *, with_msg_db: bool = True) -> Path:
    """在 root 下造一个合成的 <uin>/nt_qq 账号目录，返回 nt_qq 路径。"""
    nt_qq = root / uin / "nt_qq"
    (nt_qq / "nt_db").mkdir(parents=True)
    (nt_qq / "nt_data").mkdir(parents=True)
    if with_msg_db:
        (nt_qq / "nt_db" / "nt_msg.db").write_bytes(b"\x00" * 5120)
    return nt_qq


def _isolate_from_real_machine(tmp_path, monkeypatch):
    """切断真实的已知文件夹/注册表/USERPROFILE，使测试不依赖本机 QQ 状态。"""
    monkeypatch.setattr(discover, "documents_folder", lambda: None)
    monkeypatch.setattr(discover, "_registry_personal", lambda key: None)
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "empty"))


def test_explicit_dir_wins_over_everything(tmp_path, monkeypatch):
    root = tmp_path / "explicit"
    root.mkdir()
    nt_qq = make_account(root, "11111111")
    monkeypatch.setattr(discover, "documents_folder", lambda: tmp_path / "elsewhere")
    found = discover.discover_accounts(explicit=root)
    assert [a.uin for a in found] == ["11111111"]
    assert found[0].data_dir == nt_qq


def test_documents_folder_redirect_is_followed(tmp_path, monkeypatch):
    """本机实况：文档在 D 盘，%USERPROFILE%\\Documents 下什么都没有。"""
    redirected = tmp_path / "D_Documents" / "Tencent Files"
    redirected.mkdir(parents=True)
    make_account(redirected, "123456789")
    monkeypatch.setattr(discover, "documents_folder",
                        lambda: tmp_path / "D_Documents")
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "fakeprofile"))
    monkeypatch.setattr(discover, "_registry_personal", lambda key: None)
    found = discover.discover_accounts()
    assert [a.uin for a in found] == ["123456789"]


def test_account_fields_are_derived_correctly(tmp_path):
    root = tmp_path / "TencentFiles"
    nt_qq = make_account(root, "123456789")
    acct = discover.discover_accounts(explicit=root)[0]
    assert acct.uin == "123456789"
    assert acct.data_dir == nt_qq
    assert acct.db_dir == nt_qq / "nt_db"
    assert acct.media_dir == nt_qq / "nt_data"
    assert acct.msg_db == nt_qq / "nt_db" / "nt_msg.db"
    assert acct.msg_db.is_file()


def test_non_numeric_dirs_are_ignored(tmp_path):
    root = tmp_path / "TencentFiles"
    root.mkdir()
    make_account(root, "123456789")
    (root / "nt_qq").mkdir()                       # 全局目录，非账号
    (root / "All Users").mkdir()                  # 非数字
    (root / "System Volume Information").mkdir()  # 无权限目录名
    found = discover.discover_accounts(explicit=root)
    assert [a.uin for a in found] == ["123456789"]


def test_account_without_msg_db_is_skipped(tmp_path):
    root = tmp_path / "TencentFiles"
    root.mkdir()
    make_account(root, "11111111", with_msg_db=False)
    make_account(root, "22222222")
    assert [a.uin for a in discover.discover_accounts(explicit=root)] == ["22222222"]


def test_multiple_accounts_sorted_by_uin(tmp_path):
    root = tmp_path / "TencentFiles"
    root.mkdir()
    for uin in ("33333333", "11111111", "22222222"):
        make_account(root, uin)
    assert [a.uin for a in discover.discover_accounts(explicit=root)] == [
        "11111111", "22222222", "33333333"]


def test_discovery_error_message_contains_guidance(tmp_path, monkeypatch):
    monkeypatch.setattr(discover, "documents_folder", lambda: None)
    monkeypatch.setattr(discover, "_registry_personal", lambda key: None)
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "empty"))
    with pytest.raises(discover.DiscoveryError) as ei:
        discover.discover_accounts()
    msg = str(ei.value)
    assert "--data-dir" in msg and "Tencent Files" in msg


def test_candidate_roots_dedupes_case_insensitively(tmp_path, monkeypatch):
    same = tmp_path / "Docs"
    monkeypatch.setattr(discover, "documents_folder", lambda: same)
    monkeypatch.setattr(discover, "_registry_personal",
                        lambda key: str(same).upper())
    # 必须清掉真实 USERPROFILE，否则它额外贡献一个候选根，去重断言就不成立
    monkeypatch.delenv("USERPROFILE", raising=False)
    roots = discover.candidate_roots(explicit=same / "Tencent Files")
    assert len(roots) == 1
    assert roots[0] == same / "Tencent Files"


def test_drive_scan_finds_nested_tencent_files(tmp_path, monkeypatch):
    deep = tmp_path / "Users" / "me" / "Documents" / "Tencent Files"
    deep.mkdir(parents=True)
    make_account(deep, "123456789")
    _isolate_from_real_machine(tmp_path, monkeypatch)
    monkeypatch.setattr(discover, "_drive_roots", lambda: [tmp_path])
    found = discover.discover_accounts(scan_drives=True)
    assert [a.uin for a in found] == ["123456789"]


def test_drive_scan_prunes_noisy_dirs(tmp_path, monkeypatch):
    """System Volume Information / $RECYCLE.BIN 不得被进入（本机实测会抛权限错）。"""
    for noisy in ("System Volume Information", "$RECYCLE.BIN", "Windows"):
        (tmp_path / noisy).mkdir()
    real = tmp_path / "Docs" / "Tencent Files"
    real.mkdir(parents=True)
    make_account(real, "123456789")
    _isolate_from_real_machine(tmp_path, monkeypatch)
    monkeypatch.setattr(discover, "_drive_roots", lambda: [tmp_path])
    assert len(discover.discover_accounts(scan_drives=True)) == 1


@pytest.mark.parametrize("names,expected", [
    (["9.9.36-53644"], "9.9.36-53644"),
    (["9.9.17-31363", "9.9.36-53644"], "9.9.36-53644"),
    (["9.9.9-99999", "9.9.36-1"], "9.9.36-1"),
    ([], None),
])
def test_version_key_picks_highest(tmp_path, monkeypatch, names, expected):
    vdir = tmp_path / "QQNT" / "versions"
    vdir.mkdir(parents=True)
    for n in names:
        (vdir / n).mkdir()
    monkeypatch.setattr(discover, "_qqnt_bases", lambda: [tmp_path / "QQNT"])
    assert discover.qq_install_version() == expected


# ---------------------------------------------------------------------------
# 修正案 A §2.1：全盘扫描必须显式 opt-in，且有已访问目录上限
# ---------------------------------------------------------------------------

def test_drive_scan_not_called_without_opt_in(tmp_path, monkeypatch):
    """默认（不传 scan_drives）绝不递归扫盘。doctor/CLI 默认路径依赖此保证。"""
    def boom(*args, **kwargs):
        raise AssertionError("默认路径不得调用 find_tencent_files")
    _isolate_from_real_machine(tmp_path, monkeypatch)
    monkeypatch.setattr(discover, "find_tencent_files", boom)
    with pytest.raises(discover.NoAccountFound):
        discover.discover_accounts()


def test_find_tencent_files_truncates_at_max_dirs(tmp_path, monkeypatch):
    for i in range(5):
        (tmp_path / f"dir{i}").mkdir()
    monkeypatch.setattr(discover, "_drive_roots", lambda: [tmp_path])
    hits, truncated = discover.find_tencent_files(max_dirs=2)
    assert hits == []
    assert truncated is True


def test_truncated_scan_without_hits_raises_scan_aborted(tmp_path, monkeypatch):
    _isolate_from_real_machine(tmp_path, monkeypatch)
    monkeypatch.setattr(discover, "find_tencent_files",
                        lambda *a, **k: ([], True))
    with pytest.raises(discover.ScanAborted):
        discover.discover_accounts(scan_drives=True)


# ---------------------------------------------------------------------------
# 修正案 A §2.2：错误分类
# ---------------------------------------------------------------------------

def test_missing_msg_db_raises_database_missing(tmp_path, monkeypatch):
    root = tmp_path / "TencentFiles"
    root.mkdir()
    make_account(root, "11111111", with_msg_db=False)
    _isolate_from_real_machine(tmp_path, monkeypatch)
    with pytest.raises(discover.DatabaseMissing) as ei:
        discover.discover_accounts(explicit=root)
    msg = str(ei.value)
    assert "****1111" in msg          # 只允许掩码
    assert "11111111" not in msg      # 不得回显完整 uin


def test_permission_denied_raises_data_dir_permission_denied(tmp_path,
                                                             monkeypatch):
    root = tmp_path / "TencentFiles"
    root.mkdir()
    _isolate_from_real_machine(tmp_path, monkeypatch)
    real_scandir = os.scandir

    def denied_scandir(path, *args, **kwargs):
        if str(path) == str(root):
            raise PermissionError(13, "permission denied")
        return real_scandir(path, *args, **kwargs)

    monkeypatch.setattr(discover.os, "scandir", denied_scandir)
    with pytest.raises(discover.DataDirPermissionDenied):
        discover.discover_accounts(explicit=root)


# ---------------------------------------------------------------------------
# 修正案 A §2.3：多账号不得静默取第一个；掩码规则
# ---------------------------------------------------------------------------

def _make_accounts(tmp_path, uins):
    out = []
    for uin in uins:
        nt_qq = make_account(tmp_path / f"root-{uin}", uin)
        out.append(discover.QQAccount(uin=uin, data_dir=nt_qq,
                                      db_dir=nt_qq / "nt_db",
                                      media_dir=nt_qq / "nt_data"))
    return out


def test_select_account_returns_the_only_one(tmp_path):
    (a,) = _make_accounts(tmp_path, ["11111111"])
    assert discover.select_account([a], None) is a
    assert discover.select_account([a], "11111111") is a


def test_select_account_by_requested_uin(tmp_path):
    accts = _make_accounts(tmp_path, ["11111111", "22222222"])
    assert discover.select_account(accts, "22222222") is accts[1]


def test_select_account_refuses_to_pick_first_of_many(tmp_path):
    accts = _make_accounts(tmp_path, ["11111111", "22222222"])
    with pytest.raises(discover.AmbiguousAccountError) as ei:
        discover.select_account(accts, None)
    msg = str(ei.value)
    assert "****1111" in msg and "****2222" in msg
    assert "11111111" not in msg and "22222222" not in msg


def test_select_account_unknown_requested_uin(tmp_path):
    accts = _make_accounts(tmp_path, ["11111111", "22222222"])
    with pytest.raises(discover.AmbiguousAccountError) as ei:
        discover.select_account(accts, "33333333")
    msg = str(ei.value)
    assert "****3333" in msg and "33333333" not in msg


def test_select_account_empty_list_raises_no_account_found(tmp_path):
    with pytest.raises(discover.NoAccountFound):
        discover.select_account([], None)


def test_mask_uin():
    assert discover.mask_uin("123456789") == "****6789"
    assert discover.mask_uin("1234") == "****1234"
    assert discover.mask_uin("123") == "****"
    assert discover.mask_uin("") == "****"


# ---------------------------------------------------------------------------
# 修正案 A §2.4：is_dir 必须以 follow_symlinks=False 调用（用假 scandir 断言）
# ---------------------------------------------------------------------------

class _FakeEntry:
    def __init__(self, name, path, is_dir=True):
        self.name = name
        self.path = path
        self._is_dir = is_dir
        self.follow_symlinks_args = []

    def is_dir(self, follow_symlinks=True):
        self.follow_symlinks_args.append(follow_symlinks)
        return self._is_dir


class _FakeScandir:
    """假 os.scandir 返回值：可迭代，也支持 with 上下文协议。"""

    def __init__(self, entries):
        self._entries = entries

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def __iter__(self):
        return iter(self._entries)


def test_iter_accounts_uses_follow_symlinks_false(tmp_path, monkeypatch):
    make_account(tmp_path, "123456789")
    entry = _FakeEntry("123456789", str(tmp_path / "123456789"))
    real_scandir = os.scandir

    def fake_scandir(path, *args, **kwargs):
        if str(path) == str(tmp_path):
            return _FakeScandir([entry])
        return real_scandir(path, *args, **kwargs)

    monkeypatch.setattr(discover.os, "scandir", fake_scandir)
    found = discover._iter_accounts_under(tmp_path)[0]
    assert [a.uin for a in found] == ["123456789"]
    assert entry.follow_symlinks_args == [False]


def test_find_tencent_files_uses_follow_symlinks_false(tmp_path, monkeypatch):
    entry = _FakeEntry("somewhere", str(tmp_path / "somewhere"))
    real_scandir = os.scandir

    def fake_scandir(path, *args, **kwargs):
        if str(path) == str(tmp_path):
            return _FakeScandir([entry])
        return real_scandir(path, *args, **kwargs)

    monkeypatch.setattr(discover.os, "scandir", fake_scandir)
    monkeypatch.setattr(discover, "_drive_roots", lambda: [tmp_path])
    hits, truncated = discover.find_tencent_files()
    assert hits == [] and truncated is False
    assert entry.follow_symlinks_args == [False]


# ---------------------------------------------------------------------------
# 修正案 A §2.5：QQAccount 路径合理性检查
# ---------------------------------------------------------------------------

def test_qqaccount_rejects_inconsistent_paths(tmp_path):
    with pytest.raises(ValueError):
        discover.QQAccount(uin="123456789", data_dir=tmp_path,
                           db_dir=tmp_path / "not_nt_db",
                           media_dir=tmp_path / "nt_data")
    with pytest.raises(ValueError):
        discover.QQAccount(uin="123456789", data_dir=tmp_path,
                           db_dir=tmp_path / "nt_db",
                           media_dir=tmp_path / "not_nt_data")


def test_inconsistent_account_entry_is_skipped_not_fatal(tmp_path,
                                                         monkeypatch):
    """构造器抛 ValueError 的条目按 OSError 同等处理：跳过，继续扫。"""
    root = tmp_path / "TencentFiles"
    root.mkdir()
    make_account(root, "11111111")
    make_account(root, "22222222")
    real_cls = discover.QQAccount

    def flaky_ctor(**kwargs):
        if kwargs["uin"] == "11111111":
            raise ValueError("路径不一致")
        return real_cls(**kwargs)

    monkeypatch.setattr(discover, "QQAccount", flaky_ctor)
    found = discover.discover_accounts(explicit=root)
    assert [a.uin for a in found] == ["22222222"]
