"""自动取口令只对显式所选账号认证；测试只用合成加密页。"""
import hashlib
import pytest
from qqexport import account_access as access
from qqexport.core.discover import QQAccount
from tests.helpers import fake_sqlcipher as fake

KEY = b"#8FakeKeyForTest"
OTHER = b"#8OtherFakeKeyAA"


@pytest.fixture
def account_store(tmp_path,monkeypatch):
    account = QQAccount("12345",tmp_path,tmp_path/"nt_db",tmp_path/"nt_data")
    account.db_dir.mkdir()
    account.msg_db.write_bytes(fake.build_encrypted_db(fake.random_plain_pages(1),KEY))
    cached,saved = {},[]
    class Store:
        def load(self,uin):
            return cached.get(uin)
        def save(self,uin,key):
            saved.append((uin,key))
            cached[uin] = key
    monkeypatch.setattr(access.keystore,"KeyStore",Store)
    return account,cached,saved


def test_valid_cache_needs_no_scan(account_store,monkeypatch):
    account,cached,saved = account_store
    cached[account.uin] = KEY
    monkeypatch.setattr(access.keyscan,"scan_key",lambda verify:pytest.fail("valid cache scanned"))
    before = hashlib.sha256(account.msg_db.read_bytes()).digest()
    assert access.account_key(account,allow_scan=True)==KEY
    assert not saved
    assert hashlib.sha256(account.msg_db.read_bytes()).digest()==before


@pytest.mark.parametrize("old",[None,OTHER])
def test_missing_or_expired_cache_scans_only_for_selected_account(account_store,monkeypatch,old):
    account,cached,saved = account_store
    cached[account.uin] = old
    cached["11111111"] = OTHER
    def scan(verify):
        assert not verify(OTHER)
        assert verify(KEY)
        return KEY
    monkeypatch.setattr(access.keyscan,"scan_key",scan)
    assert access.account_key(account,allow_scan=True)==KEY
    assert saved==[(account.uin,KEY)]
    assert cached["11111111"]==OTHER


def test_ordinary_startup_never_scans(account_store,monkeypatch):
    account,_,saved = account_store
    monkeypatch.setattr(access.keyscan,"scan_key",lambda verify:pytest.fail("implicit scan"))
    with pytest.raises(access.AccountAccessError) as error:
        access.account_key(account)
    assert error.value.code=="missing" and not saved


@pytest.mark.parametrize("outcome",[None,OTHER,"denied"])
def test_scan_failure_never_caches_wrong_candidate(account_store,monkeypatch,outcome):
    account,_,saved = account_store
    def scan(verify):
        if outcome=="denied":
            raise access.keyscan.ProcessAccessDenied()
        return outcome
    monkeypatch.setattr(access.keyscan,"scan_key",scan)
    with pytest.raises(access.AccountAccessError) as error:
        access.account_key(account,allow_scan=True)
    assert error.value.code==("permission" if outcome=="denied" else "not_found")
    assert not saved
