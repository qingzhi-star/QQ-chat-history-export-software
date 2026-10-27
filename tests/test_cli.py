"""cli 的单元测试。

全部用 uin=12345 的合成账号并 monkeypatch 掉 discover，脱离本机真实 QQ。
覆盖修正案 A §10 的四类：移除口令位置参数、getpass 回显失效即中止、
非 TTY / cp936 / --ascii 编码路径、多账号不默认取第一个。
"""
import getpass
import io
import json
import sys
import warnings

import pytest

from qqexport import __version__, cli
from qqexport.core.keystore import fingerprint


def synthetic_account(tmp_path):
    """一个 uin=12345 的合成账号，使全部 CLI 测试脱离本机真实 QQ 环境。"""
    from qqexport.core.discover import QQAccount
    db_dir = tmp_path / "TencentFiles" / "12345" / "nt_qq" / "nt_db"
    db_dir.mkdir(parents=True, exist_ok=True)
    (db_dir / "nt_msg.db").write_bytes(b"\x00" * 5120)
    return QQAccount("12345", db_dir.parent, db_dir,
                     db_dir.parent / "nt_data")


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("QQEXPORT_HOME", str(tmp_path / "apphome"))
    account = synthetic_account(tmp_path)
    monkeypatch.setattr(cli.discover, "discover_accounts",
                        lambda **kw: [account])
    return account


def run(argv, capsys):
    code = cli.main(argv)
    out = capsys.readouterr()
    return code, out.out + out.err


def _fake_report(status="ok", name="python", detail="3.14.6"):
    return cli.report.DoctorReport(
        [cli.report.CheckResult(1, name, status, detail)], "t")


# ---------- 基本命令 ----------

def test_version_prints_and_exits_zero(capsys):
    code, text = run(["version"], capsys)
    assert code == 0 and __version__ in text


def test_unknown_command_exits_two(capsys):
    with pytest.raises(SystemExit) as ei:
        cli.main(["bogus"])
    assert ei.value.code == 2


def test_parser_has_all_p0_subcommands():
    text = cli.build_parser().format_help()
    assert "doctor" in text and "key" in text and "version" in text


# ---------- doctor ----------

def test_doctor_json_is_valid_and_exit_code_tracks_ok(capsys, monkeypatch):
    monkeypatch.setattr(cli.doctor, "run_doctor", lambda **kw: _fake_report())
    code, text = run(["doctor", "--json"], capsys)
    doc = json.loads(text)
    assert doc["ok"] is True and code == 0


def test_doctor_json_exit_one_when_a_check_fails(capsys, monkeypatch):
    monkeypatch.setattr(cli.doctor, "run_doctor",
                        lambda **kw: _fake_report("fail", detail="bad"))
    code, text = run(["doctor", "--json"], capsys)
    assert json.loads(text)["ok"] is False
    assert code == 1


def test_doctor_text_output_is_human_readable(capsys, monkeypatch):
    monkeypatch.setattr(cli.doctor, "run_doctor", lambda **kw: _fake_report())
    code, text = run(["doctor"], capsys)
    assert "python" in text and "3.14.6" in text and code == 0


def test_doctor_ascii_flag_produces_pure_ascii(capsys, monkeypatch):
    monkeypatch.setattr(cli.doctor, "run_doctor", lambda **kw: _fake_report())
    _, text = run(["doctor", "--ascii"], capsys)
    assert text.encode("ascii", "strict")


def test_doctor_passes_flags_through(capsys, monkeypatch):
    seen = {}

    def fake(**kw):
        seen.update(kw)
        return cli.report.DoctorReport([], "t")

    monkeypatch.setattr(cli.doctor, "run_doctor", fake)
    run(["doctor", "--uin", "12345", "--scan", "--timeout", "7",
         "--keep-output"], capsys)
    assert seen["uin"] == "12345" and seen["allow_scan"] is True
    assert seen["scan_timeout_s"] == 7.0 and seen["keep_output"] is True
    assert seen["work_dir"] is not None          # keep_output 必须落盘到明确目录
    assert seen["scan_drives"] is False


def test_doctor_scan_drives_flag_is_opt_in(capsys, monkeypatch):
    seen = {}

    def fake(**kw):
        seen.update(kw)
        return cli.report.DoctorReport([], "t")

    monkeypatch.setattr(cli.doctor, "run_doctor", fake)
    run(["doctor", "--scan-drives"], capsys)
    assert seen["scan_drives"] is True


# ---------- key set / show / clear ----------

def test_key_show_on_empty_store(capsys):
    code, text = run(["key", "show"], capsys)
    assert code == 0 and ("没有" in text or "no " in text.lower())


def test_key_set_from_stdin_then_show(capsys, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO("#8FakeKeyForTest\n"))
    code, _ = run(["key", "set", "--uin", "12345", "--from-stdin"], capsys)
    assert code == 0
    code, text = run(["key", "show"], capsys)
    assert code == 0 and "12345" in text
    assert "#8FakeKeyForTest" not in text            # 只出指纹
    assert fingerprint(b"#8FakeKeyForTest") in text


def test_key_set_rejects_positional_passphrase(capsys):
    """修正案 A §10.1：位置参数会进 shell history / ps，必须拒绝。"""
    with pytest.raises(SystemExit) as ei:
        cli.main(["key", "set", "#8FakeKeyForTest", "--uin", "12345"])
    assert ei.value.code == 2


def test_key_set_empty_stdin_is_rejected(capsys, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    code, text = run(["key", "set", "--uin", "12345", "--from-stdin"], capsys)
    assert code == 1
    assert "为空" in text


def test_key_set_getpass_warning_aborts(capsys, monkeypatch):
    """修正案 A §10.2：回显关不掉时必须提示并中止，不得继续读。"""
    def fake_getpass(prompt=""):
        warnings.warn("Can not control echo on the terminal.",
                      getpass.GetPassWarning)
        return "secret-should-not-be-saved"

    monkeypatch.setattr(cli.getpass, "getpass", fake_getpass)
    code, text = run(["key", "set"], capsys)
    assert code == 1
    assert "无法关闭回显" in text
    code, text = run(["key", "show"], capsys)
    assert "没有" in text


def test_key_clear_reports_count(capsys, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO("#8FakeKeyForTest\n"))
    run(["key", "set", "--uin", "12345", "--from-stdin"], capsys)
    code, text = run(["key", "clear", "--uin", "12345"], capsys)
    assert code == 0 and "1" in text
    _, text = run(["key", "show"], capsys)
    assert "12345" not in text


def test_key_show_corrupt_store_reports_error_without_traceback(tmp_path, capsys):
    home = tmp_path / "apphome"
    home.mkdir(parents=True, exist_ok=True)
    (home / "keys.json").write_text("{not json", encoding="utf-8")
    code, text = run(["key", "show"], capsys)
    assert code == 1
    assert "keys.json" in text and ".bak" in text
    assert "Traceback" not in text


# ---------- key scan ----------

def test_key_scan_reports_failure_gracefully(capsys, monkeypatch):
    monkeypatch.setattr(cli.keyscan, "find_qq_pids", lambda *a, **k: [])
    code, text = run(["key", "scan"], capsys)
    assert code == 1
    assert "QQ" in text


def test_key_scan_never_prints_the_key(capsys, monkeypatch):
    from qqexport.core import sqlcipher as sc
    from tests.helpers import fake_sqlcipher as fake

    secret = b"#8FakeKeyForTest"
    blob = fake.build_encrypted_db(fake.random_plain_pages(1), secret)
    monkeypatch.setattr(cli.sqlcipher, "read_header_and_page1",
                        lambda p: blob[:sc.WRAPPER_SZ + sc.PAGE_SZ])
    monkeypatch.setattr(cli.keyscan, "scan_key", lambda verify, **kw: secret)
    code, text = run(["key", "scan"], capsys)
    assert code == 0
    assert secret.decode() not in text
    assert fingerprint(secret) in text


def test_multiple_accounts_hint_uin(tmp_path, capsys, monkeypatch):
    """修正案 A §10.4：多账号不得默认第一个；要提示 --uin 并退出 1。"""
    account = synthetic_account(tmp_path)
    from qqexport.core.discover import QQAccount
    other = QQAccount("11111111", account.data_dir, account.db_dir,
                      account.media_dir)
    monkeypatch.setattr(cli.discover, "discover_accounts",
                        lambda **kw: [account, other])
    code, text = run(["key", "scan"], capsys)
    assert code == 1
    assert "****" in text
    assert "--uin" in text


# ---------- 编码 / 非 TTY 路径（修正案 A §10.3） ----------

class _FakeStdout:
    def __init__(self):
        self.parts = []

    def write(self, text):
        self.parts.append(text)

    def flush(self):
        pass


def test_stdout_without_reconfigure_does_not_crash(monkeypatch):
    fake = _FakeStdout()
    monkeypatch.setattr(sys, "stdout", fake)
    assert cli.main(["version"]) == 0
    assert "qqexport" in "".join(fake.parts)


def test_non_tty_output_has_no_ansi(monkeypatch):
    fake = _FakeStdout()
    monkeypatch.setattr(sys, "stdout", fake)
    monkeypatch.setattr(cli.doctor, "run_doctor", lambda **kw: _fake_report())
    assert cli.main(["doctor"]) == 0
    assert "\x1b[" not in "".join(fake.parts)


def test_cp936_stdout_does_not_raise(monkeypatch):
    wrapped = io.TextIOWrapper(io.BytesIO(), encoding="cp936")
    monkeypatch.setattr(sys, "stdout", wrapped)
    assert cli.main(["version"]) == 0
