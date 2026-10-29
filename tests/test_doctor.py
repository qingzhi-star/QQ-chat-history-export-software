"""doctor 的单元测试。

合成库是随机页，quick_check 必然不 ok（见修正案 A §8.6 / R30），所以需要
「解密成功」语义的测试统一把 `decrypt.integrity_probe` 打桩成 ok；`failed`
分支另用真实 probe 的用例覆盖（Task 8 已单测）。这里验证的是 11 项检查的
状态映射、脱敏、临时目录生命周期与截断信号。

全部测试用合成账号并 monkeypatch 掉发现/进程/版本边界，不接触真实 QQ。
"""
import json
from pathlib import Path

import pytest

from qqexport import doctor
from qqexport.core import decrypt as dec
from qqexport.core import sqlcipher as sc
from qqexport.core.discover import DiscoveryError, QQAccount
from qqexport.core.keystore import KeyStore, fingerprint
from tests.helpers import fake_sqlcipher as fake

PASSPHRASE = b"#8FakeKeyForTest"


def build_env(tmp_path: Path, monkeypatch, *, n_pages: int = 4,
              with_wal: bool = True, qq_running: bool = True,
              uin: str = "123456789") -> QQAccount:
    """造一个能通过全部 11 项检查的合成环境（integrity_probe 需另打桩）。"""
    account_dir = tmp_path / "TencentFiles" / uin / "nt_qq"
    db_dir = account_dir / "nt_db"
    media = account_dir / "nt_data"
    db_dir.mkdir(parents=True)
    media.mkdir(parents=True)

    pages = fake.random_plain_pages(n_pages)
    blob = fake.build_encrypted_db(pages, PASSPHRASE)
    db = db_dir / "nt_msg.db"
    db.write_bytes(blob)
    salt = blob[sc.WRAPPER_SZ:sc.WRAPPER_SZ + sc.SALT_SZ]
    aes, mac = sc.derive_keys(PASSPHRASE, salt, "sha512")
    if with_wal:
        (db_dir / "nt_msg.db-wal").write_bytes(
            fake.build_wal([(2, pages[1], n_pages + 1)], aes_key=aes,
                           mac_key=mac, mac="sha1", reserve=48, db_salt=salt))
        (db_dir / "nt_msg.db-shm").write_bytes(b"\x00" * 136)

    account = QQAccount(uin=uin, data_dir=account_dir, db_dir=db_dir,
                        media_dir=media)
    monkeypatch.setattr(doctor, "_discover", lambda **kw: [account])
    monkeypatch.setattr(doctor, "_qq_version", lambda: "9.9.36-53644")
    monkeypatch.setattr(doctor, "_qq_pids", lambda: [4242] if qq_running else [])
    monkeypatch.setenv("QQEXPORT_HOME", str(tmp_path / "apphome"))
    return account


@pytest.fixture
def probe_ok(monkeypatch):
    """把 quick_check 打桩成 ok；可选返回的表名。"""
    def _set(tables=("c2c_msg_table", "group_msg_table")):
        monkeypatch.setattr(dec, "integrity_probe",
                            lambda path: ("ok", list(tables)))
    return _set


def test_all_checks_pass_on_healthy_synthetic_env(tmp_path, monkeypatch, probe_ok):
    build_env(tmp_path, monkeypatch)
    probe_ok()
    rep = doctor.run_doctor(passphrase=PASSPHRASE, work_dir=tmp_path / "work")
    assert len(rep.checks) == 11
    assert [c.index for c in rep.checks] == list(range(1, 12))
    assert rep.ok, [(c.index, c.name, c.status, c.detail) for c in rep.checks]


def test_check_names_match_check_specs(tmp_path, monkeypatch, probe_ok):
    build_env(tmp_path, monkeypatch)
    probe_ok()
    rep = doctor.run_doctor(passphrase=PASSPHRASE, work_dir=tmp_path / "work")
    assert [c.name for c in rep.checks] == [s.name for s in doctor.CHECK_SPECS]
    assert [s.index for s in doctor.CHECK_SPECS] == list(range(1, 12))


def test_no_check_detail_leaks_the_passphrase(tmp_path, monkeypatch, probe_ok):
    build_env(tmp_path, monkeypatch)
    probe_ok()
    rep = doctor.run_doctor(passphrase=PASSPHRASE, work_dir=tmp_path / "work")
    text = rep.to_text() + repr(rep.to_json())
    assert PASSPHRASE.decode() not in text
    assert fingerprint(PASSPHRASE) in text        # 指纹允许出现，本体绝不允许


def test_check8_reports_detected_cipher_params(tmp_path, monkeypatch, probe_ok):
    build_env(tmp_path, monkeypatch)
    probe_ok()
    rep = doctor.run_doctor(passphrase=PASSPHRASE, work_dir=tmp_path / "work")
    assert "sha512/sha1/48" in rep.checks[7].detail


def test_check9_produces_queryable_plaintext_db(tmp_path, monkeypatch, probe_ok):
    build_env(tmp_path, monkeypatch)
    probe_ok()
    rep = doctor.run_doctor(passphrase=PASSPHRASE, work_dir=tmp_path / "work",
                            keep_output=True)
    assert rep.checks[8].status == "ok"
    out = list((tmp_path / "work").rglob("*.decrypted.db"))
    assert len(out) == 1
    assert out[0].read_bytes()[:16] == sc.SQLITE_MAGIC
    assert out[0].stat().st_size % sc.PAGE_SZ == 0


def test_check10_reports_wal_frames(tmp_path, monkeypatch, probe_ok):
    build_env(tmp_path, monkeypatch)
    probe_ok()
    rep = doctor.run_doctor(passphrase=PASSPHRASE, work_dir=tmp_path / "work")
    assert rep.checks[9].status == "ok"
    assert "1" in rep.checks[9].detail


def test_check10_warns_when_wal_absent(tmp_path, monkeypatch, probe_ok):
    build_env(tmp_path, monkeypatch, with_wal=False)
    probe_ok()
    rep = doctor.run_doctor(passphrase=PASSPHRASE, work_dir=tmp_path / "work")
    assert rep.checks[9].status == "warn"
    assert rep.ok is True


def test_status_lossy_warns_with_reasons(tmp_path, monkeypatch, probe_ok):
    build_env(tmp_path, monkeypatch)
    probe_ok()
    (tmp_path / "TencentFiles" / "123456789" / "nt_qq" / "nt_db"
     / "nt_msg.db-wal").write_bytes(b"\x00" * 64)      # 结构无效的 WAL
    rep = doctor.run_doctor(passphrase=PASSPHRASE, work_dir=tmp_path / "work")
    assert rep.checks[8].status == "warn"
    assert "降级原因" in rep.checks[8].detail
    assert rep.checks[9].status == "warn"
    assert rep.ok is True


def test_missing_qq_process_is_warn_not_fail(tmp_path, monkeypatch, probe_ok):
    build_env(tmp_path, monkeypatch, qq_running=False)
    probe_ok()
    rep = doctor.run_doctor(passphrase=PASSPHRASE, work_dir=tmp_path / "work")
    assert rep.checks[5].status == "warn"
    assert rep.ok is True


def test_qq_not_running_and_no_key_fails_check6(tmp_path, monkeypatch):
    build_env(tmp_path, monkeypatch, qq_running=False)
    rep = doctor.run_doctor(work_dir=tmp_path / "work")
    assert rep.checks[5].status == "fail"
    assert rep.ok is False
    assert all(c.status == "skip" for c in rep.checks[6:])


def test_qq_not_running_but_cached_key_is_warn(tmp_path, monkeypatch):
    """修正案 A §9.4：QQ 未运行但有可用缓存密钥 → warn（不是 fail）。"""
    build_env(tmp_path, monkeypatch, qq_running=False)
    ks = KeyStore(tmp_path / "apphome" / "keys.json",
                  protect=lambda d: bytes(b ^ 0x5A for b in d),
                  unprotect=lambda b: bytes(b ^ 0x5A for b in b))
    ks.save("123456789", PASSPHRASE)
    monkeypatch.setattr(doctor, "_key_store", lambda: ks)
    monkeypatch.setattr(dec, "integrity_probe",
                        lambda path: ("ok", ["c2c_msg_table", "group_msg_table"]))
    rep = doctor.run_doctor(work_dir=tmp_path / "work")
    assert rep.checks[5].status == "warn"
    assert rep.checks[6].status == "ok"
    assert rep.ok is True


def test_check9_fails_when_product_broken(tmp_path, monkeypatch):
    """真实 quick_check（不打桩）：随机页产物必然损坏 → 检查 9 fail，10/11 skip。"""
    build_env(tmp_path, monkeypatch)
    rep = doctor.run_doctor(passphrase=PASSPHRASE, work_dir=tmp_path / "work")
    assert rep.checks[8].status == "fail"
    assert rep.ok is False
    assert rep.checks[9].status == "skip"
    assert rep.checks[10].status == "skip"


def test_wrong_passphrase_fails_check7_and_skips_dependents(tmp_path, monkeypatch):
    build_env(tmp_path, monkeypatch)
    rep = doctor.run_doctor(passphrase=b"#8WrongKeyXXXXXX",
                            work_dir=tmp_path / "work")
    assert rep.checks[6].status == "fail"
    assert rep.ok is False
    assert len(rep.checks) == 11
    assert all(c.status == "skip" for c in rep.checks[7:])
    assert "WrongKey" not in rep.to_text()


def test_no_passphrase_and_no_scan_fails_check7(tmp_path, monkeypatch):
    build_env(tmp_path, monkeypatch)
    rep = doctor.run_doctor(allow_scan=False, work_dir=tmp_path / "work")
    assert rep.checks[6].status == "fail"
    assert rep.ok is False


def test_cached_passphrase_is_used_when_none_given(tmp_path, monkeypatch, probe_ok):
    build_env(tmp_path, monkeypatch)
    probe_ok()
    ks = KeyStore(tmp_path / "apphome" / "keys.json",
                  protect=lambda d: bytes(b ^ 0x5A for b in d),
                  unprotect=lambda b: bytes(b ^ 0x5A for b in b))
    ks.save("123456789", PASSPHRASE)
    monkeypatch.setattr(doctor, "_key_store", lambda: ks)
    rep = doctor.run_doctor(work_dir=tmp_path / "work")
    assert rep.checks[6].status == "ok"
    assert "缓存" in rep.checks[6].detail


def test_discovery_failure_fails_check3_and_skips_rest(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor, "_discover",
                        lambda **kw: (_ for _ in ()).throw(DiscoveryError("没找到")))
    monkeypatch.setattr(doctor, "_qq_version", lambda: "9.9.36-53644")
    monkeypatch.setattr(doctor, "_qq_pids", lambda: [])
    monkeypatch.setenv("QQEXPORT_HOME", str(tmp_path / "apphome"))
    rep = doctor.run_doctor(passphrase=PASSPHRASE, work_dir=tmp_path / "work")
    assert rep.checks[0].status == "ok"          # python 版本检查仍通过
    assert rep.checks[2].status == "fail"
    assert all(c.status == "skip" for c in rep.checks[3:])


def test_multiple_accounts_require_explicit_uin(tmp_path, monkeypatch):
    account_a = build_env(tmp_path, monkeypatch)
    account_b = QQAccount(uin="11111111", data_dir=account_a.data_dir,
                          db_dir=account_a.db_dir,
                          media_dir=account_a.media_dir)
    monkeypatch.setattr(doctor, "_discover", lambda **kw: [account_a, account_b])
    rep = doctor.run_doctor(passphrase=PASSPHRASE, work_dir=tmp_path / "work")
    assert rep.checks[3].status == "fail"
    assert "--uin" in rep.checks[3].detail
    assert rep.checks[4].status == "skip"


def test_db_size_shape_check_detects_non_qq_file(tmp_path, monkeypatch):
    account = build_env(tmp_path, monkeypatch)
    account.msg_db.write_bytes(b"\x00" * 4096)    # 不再是 %4096==1024
    rep = doctor.run_doctor(passphrase=PASSPHRASE, work_dir=tmp_path / "work")
    assert rep.checks[4].status == "fail"


def test_exception_inside_a_check_becomes_fail_not_crash(tmp_path, monkeypatch):
    build_env(tmp_path, monkeypatch)

    def boom():
        raise RuntimeError("secret detail that must not leak")

    monkeypatch.setattr(doctor, "_qq_version", boom)
    rep = doctor.run_doctor(passphrase=PASSPHRASE, work_dir=tmp_path / "work")
    assert rep.checks[1].status == "fail"
    assert "RuntimeError" in rep.checks[1].detail
    assert "secret detail" not in rep.to_text()


def test_keyboard_interrupt_propagates(tmp_path, monkeypatch):
    """修正案 A §9.6：_run 不得捕获 BaseException，Ctrl+C 必须往外抛。"""
    build_env(tmp_path, monkeypatch)

    def interrupted():
        raise KeyboardInterrupt()

    monkeypatch.setattr(doctor, "_qq_version", interrupted)
    with pytest.raises(KeyboardInterrupt):
        doctor.run_doctor(passphrase=PASSPHRASE, work_dir=tmp_path / "work")


def test_check11_reports_expected_tables(tmp_path, monkeypatch, probe_ok):
    build_env(tmp_path, monkeypatch)
    probe_ok(())                                  # 解不出任何表
    rep = doctor.run_doctor(passphrase=PASSPHRASE, work_dir=tmp_path / "work",
                            keep_output=True)
    assert rep.checks[10].status == "warn"
    assert "c2c_msg_table" in rep.checks[10].detail
    assert "group_msg_table" in rep.checks[10].detail
    assert "缺失" in rep.checks[10].detail


def test_keep_output_false_removes_plaintext_db(tmp_path, monkeypatch, probe_ok):
    build_env(tmp_path, monkeypatch)
    probe_ok()
    doctor.run_doctor(passphrase=PASSPHRASE, work_dir=tmp_path / "work",
                      keep_output=False)
    assert list((tmp_path / "work").rglob("*.decrypted.db")) == []


def test_default_work_dir_is_temp_and_cleaned(tmp_path, monkeypatch, probe_ok):
    """默认不得写 config.work_dir；临时目录退出后必须消失（§9.2）。"""
    build_env(tmp_path, monkeypatch)
    probe_ok()
    created = []
    real = doctor.tempfile.TemporaryDirectory

    def tracking(*args, **kwargs):
        td = real(*args, **kwargs)
        created.append(td)             # 持有对象，防止 __del__ 顺手清理掉
        return td

    monkeypatch.setattr(doctor.tempfile, "TemporaryDirectory", tracking)
    rep = doctor.run_doctor(passphrase=PASSPHRASE)
    assert rep.ok
    assert created
    assert not Path(created[0].name).exists()   # 必须由 run_doctor 自己清理
    assert not (tmp_path / "apphome" / "work").exists()


def test_capacity_estimate_warns_when_disk_short(tmp_path, monkeypatch, probe_ok):
    build_env(tmp_path, monkeypatch)
    probe_ok()

    class _Usage:
        free = 1

    monkeypatch.setattr(doctor.shutil, "disk_usage", lambda path: _Usage())
    rep = doctor.run_doctor(passphrase=PASSPHRASE, work_dir=tmp_path / "work")
    assert rep.checks[8].status == "warn"
    detail = rep.checks[8].detail
    assert "B" in detail and "需要" in detail and "可用" in detail
    assert rep.checks[9].status == "skip"
    assert rep.ok is True


def test_default_doctor_does_not_scan_drives(tmp_path, monkeypatch, probe_ok):
    """修正案 A §2.1：默认 doctor 不得触发盘符递归扫描。"""
    build_env(tmp_path, monkeypatch)
    probe_ok()

    def must_not_scan(*args, **kwargs):
        raise AssertionError("默认 doctor 不得调用 find_tencent_files")

    monkeypatch.setattr(doctor.disc, "find_tencent_files", must_not_scan)
    rep = doctor.run_doctor(passphrase=PASSPHRASE, work_dir=tmp_path / "work")
    assert rep.checks[2].status == "ok"


def test_scan_drives_reports_truncation(tmp_path, monkeypatch, probe_ok):
    """修正案 A §9.7：显式扫盘时截断事实必须进检查 3 的 detail。"""
    build_env(tmp_path, monkeypatch)
    probe_ok()
    monkeypatch.setattr(doctor.disc, "find_tencent_files",
                        lambda **kwargs: ([], True))
    rep = doctor.run_doctor(passphrase=PASSPHRASE, work_dir=tmp_path / "work",
                            scan_drives=True)
    assert rep.checks[2].status == "warn"
    assert "上限" in rep.checks[2].detail
    assert "可能未列出全部账号" in rep.checks[2].detail


def test_json_has_schema_version_and_redaction(tmp_path, monkeypatch, probe_ok):
    build_env(tmp_path, monkeypatch)
    probe_ok()
    rep = doctor.run_doctor(passphrase=PASSPHRASE, work_dir=tmp_path / "work")
    doc = rep.to_json()
    assert doc["schema_version"] == 1
    text = json.dumps(doc, ensure_ascii=False)
    assert str(tmp_path) not in text
    assert "123456789" not in text
    assert "****6789" in text
    assert "<Documents>" in text


def test_expected_tables_and_columns_match_spec_section_7_1():
    assert "c2c_msg_table" in doctor.EXPECTED_TABLES
    assert "group_msg_table" in doctor.EXPECTED_TABLES
    for col in ("40001", "40011", "40020", "40050", "40800"):
        assert col in doctor.EXPECTED_COLUMNS
