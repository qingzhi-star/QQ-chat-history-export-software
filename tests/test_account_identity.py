import os
import shutil
import pytest
from qqexport import account_panel as panel
from qqexport.core.discover import QQAccount
from tests.helpers import synth_db as sd
from qqexport.qq import msgmap as mm


def test_identity_reads_self_nickname_and_cleans_workcopy(tmp_path,monkeypatch):
    monkeypatch.setenv("QQEXPORT_HOME",str(tmp_path/"home"))
    account = QQAccount("12345",tmp_path,tmp_path/"nt_db",tmp_path/"nt_data")
    profile = sd.create_companion_db(tmp_path/"synthetic-profile.db",kind="profile",rows={
        mm.PROFILE_TABLE:[{mm.PROFILE_UID:"u_self",mm.PROFILE_UIN:"12345",mm.PROFILE_NICK:"合成账号昵称"}]})
    monkeypatch.setattr(panel.keystore.KeyStore,"load",lambda self,uin:b"synthetic")
    def decrypt(source,key,directory):
        output = directory/"plain-profile.db"
        shutil.copyfile(profile,output)
        return output
    monkeypatch.setattr(panel.companions,"decrypt_companion",decrypt)
    monkeypatch.setattr(panel.avatars,"fetch_avatar",lambda uin:b"synthetic-avatar")
    monkeypatch.setattr(panel,"database_in_use",lambda account:True)
    identity = panel.read_identity(account)
    assert identity==panel.Identity("12345","合成账号昵称",b"synthetic-avatar",True)
    assert not list((tmp_path/"home"/"work"/"profiles").glob(".tmp-*"))


def test_missing_profile_uses_number_without_scanning_memory(tmp_path,monkeypatch):
    account = QQAccount("12345",tmp_path,tmp_path/"nt_db",tmp_path/"nt_data")
    monkeypatch.setattr(panel.keystore.KeyStore,"load",lambda self,uin:None)
    monkeypatch.setattr(panel.companions,"decrypt_companion",lambda *args:pytest.fail("no cached key"))
    monkeypatch.setattr(panel.avatars,"fetch_avatar",lambda uin:None)
    monkeypatch.setattr(panel,"database_in_use",lambda account:False)
    assert panel.read_identity(account)==panel.Identity("12345")


def test_self_name_falls_back_to_member_nickname_not_group_card():
    from qqexport.qq.contacts import Contacts,Member,Friend
    book = Contacts(account_uin="12345")
    book.members_by_uin[("synthetic_group","12345")] = Member(uin="12345",nick="合成全局昵称",card="合成群名片")
    assert panel.profile_name(book,"12345")=="合成全局昵称"
    book.friends_by_uin["12345"] = Friend(uin="12345",nick="合成本机昵称")
    assert panel.profile_name(book,"12345")=="合成本机昵称"


@pytest.mark.skipif(os.name!="nt",reason="Windows file sharing probe")
def test_active_probe_uses_file_sharing_without_reading_contents(tmp_path):
    import ctypes
    from ctypes import wintypes
    account = QQAccount("12345",tmp_path,tmp_path/"nt_db",tmp_path/"nt_data")
    account.db_dir.mkdir()
    account.msg_db.write_bytes(b"synthetic")
    assert not panel.database_in_use(account)
    kernel = ctypes.WinDLL("kernel32",use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateFileW(str(account.msg_db),0x80000000,1,None,3,128,None)
    assert handle!=ctypes.c_void_p(-1).value
    try:
        assert panel.database_in_use(account)
    finally:
        kernel.CloseHandle(handle)
    assert account.msg_db.read_bytes()==b"synthetic"
