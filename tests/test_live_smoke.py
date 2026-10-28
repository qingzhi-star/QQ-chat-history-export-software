"""真机端到端冒烟。默认跳过，用 `pytest -m live -v -s` 运行。

这是 P0 验收（spec §10.3）的执行载体。对真实 QQ 数据**只读**；所有明文产物
写入 `tempfile.TemporaryDirectory()`，退出上下文即清理（修正案 A §12.6）。

按修正案 A §12 的关键设计：

- 账号优先取 `Config.load().uin`；未配置且恰好一个账号才自动选，多账号 skip。
  任何提示只出现掩码 `****<后4位>`。
- WAL 恢复用**同一份快照**做「含 WAL / 不含 WAL」两次解密，按已提交帧的 pgno
  逐页比较字节差异——相等不再被当作「WAL 生效」（§12.2）。
- 确定性比较对同一份快照重复解密，并用流式 SHA-256（§12.4/§12.5）。
- 只读性分条件：QQ 运行中只断言「无文件被删除」，有变化则 SKIP；QQ 退出后
  identity 三元组不变才是 PASS（§12.7）。
- cipher 指纹只要求 ∈ 八组合，与预期不符时打印 WARN 而非失败（§12.8）。
"""
from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import time
import tracemalloc
from pathlib import Path

import pytest

from qqexport import config, doctor
from qqexport.core import decrypt as dec
from qqexport.core import discover as disc
from qqexport.core import keyscan
from qqexport.core import keystore
from qqexport.core import snapshot as snap
from qqexport.core import sqlcipher as sc
from qqexport.core import wal

pytestmark = pytest.mark.live

CHUNK = 1024 * 1024
EXPECTED_FP = "sha512/sha1/48"
MSG_TABLE_HINTS = ("c2c_msg_table", "group_msg_table")


# ---------- fixtures ----------

@pytest.fixture(scope="module")
def account() -> disc.QQAccount:
    cfg = config.Config.load()
    accounts = disc.discover_accounts()
    if cfg.uin:
        for a in accounts:
            if a.uin == cfg.uin:
                return a
        pytest.skip(f"配置里的账号 {disc.mask_uin(cfg.uin)} 不在已发现账号中")
    if len(accounts) == 1:
        return accounts[0]
    pytest.skip(f"发现 {len(accounts)} 个账号；请先在配置里指定 uin"
                "（或只保留一个账号）后重跑")


@pytest.fixture(scope="module")
def passphrase(account) -> bytes:
    cached = keystore.KeyStore().load(account.uin)
    if cached:
        return cached
    head = sc.read_header_and_page1(account.msg_db)
    found = keyscan.scan_key(
        lambda cand: sc.detect_params(cand, head) is not None, timeout_s=300.0)
    if found is None:
        pytest.skip("内存扫描未找到口令；请先启动并登录 QQ，或运行 "
                    "`python -m qqexport key scan` 后重跑")
    return found


@pytest.fixture()
def workdir():
    """明文产物生命周期：退出上下文即删除，删除失败必须让测试失败。"""
    td = tempfile.TemporaryDirectory(prefix="qqexport-live-")
    path = Path(td.name)
    try:
        yield path
    finally:
        td.cleanup()
        assert not path.exists(), "临时目录未被清理"


# ---------- 流式辅助（§12.5） ----------

def _read_magic(path: Path) -> bytes:
    with open(path, "rb") as f:
        return f.read(16)


def _sha256_stream(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(CHUNK)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _connect_ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)


def _source_fingerprint(account) -> dict[str, tuple[int, int, int]]:
    """相对路径为键（§12.3）；identity = (size, mtime_ns, file index)。"""
    out: dict[str, tuple[int, int, int]] = {}
    for p in account.db_dir.rglob("*"):
        if p.is_file():
            st = p.stat()
            out[str(p.relative_to(account.db_dir))] = (
                st.st_size, st.st_mtime_ns, st.st_ino)
    return out


# ---------- 基础链路 ----------

def test_live_discover_finds_account(account):
    print(f"\naccount: {disc.mask_uin(account.uin)}")
    assert account.uin.isdigit()
    assert account.msg_db.is_file()
    size = account.msg_db.stat().st_size
    assert sc.has_wrapper_header(size)
    print(f"nt_msg.db: {size:,} B, {sc.page_count_from_size(size):,} pages")


def test_live_qq_version_is_reported():
    version = disc.qq_install_version()
    print(f"\nQQ version: {version}")
    assert version is not None


def test_live_detect_params_matches_known_combo(account, passphrase):
    head = sc.read_header_and_page1(account.msg_db)
    params = sc.detect_params(passphrase, head)
    assert params is not None, "八组合均未通过真库 page-1 HMAC 校验"
    assert (params.kdf, params.mac, params.reserve) in sc.VERIFY_COMBOS, \
        f"命中了表外组合：{params.fingerprint}"
    print(f"\ncipher fingerprint: {params.fingerprint}")
    if params.fingerprint != EXPECTED_FP:
        print(f"WARN: 预期 {EXPECTED_FP}，实际 {params.fingerprint}"
              "（按 §3.2 记观测值，不算失败）")


def test_live_decrypt_produces_queryable_db(account, passphrase, workdir):
    res = dec.decrypt_database(account, passphrase, dst_dir=workdir / "work")
    db = res.plaintext_db
    assert db.is_file()
    assert db.stat().st_size % sc.PAGE_SZ == 0
    assert _read_magic(db) == sc.SQLITE_MAGIC

    con = _connect_ro(db)
    try:
        assert con.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")]
        missing = [t for t in MSG_TABLE_HINTS if t not in tables]
        assert not missing, f"缺少消息表 {missing}；实际表名：{tables[:40]}"
        counts = {t: con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                  for t in MSG_TABLE_HINTS}
    finally:
        con.close()

    print(f"\nstatus={res.status} pages={res.page_count:,} "
          f"corrupt={res.corrupt_pages} counts={counts} "
          f"wal={res.wal_reason}/{res.wal_frames}")
    assert res.corrupt_pages <= res.page_count * 0.01, \
        f"损坏页占比过高：{res.corrupt_pages}/{res.page_count}"
    if res.status == "failed":
        pytest.fail(f"解密判定 failed：{res.warnings}")
    if sum(counts.values()) == 0:
        pytest.skip("该账号库中没有消息，无法验证导出内容；请换有消息的账号")


# ---------- WAL 恢复（核心修正 §12.2） ----------

def _committed_pgnos(wal_path: Path, last_commit_frame: int) -> list[int]:
    with open(wal_path, "rb") as f:
        hdr = wal.parse_header(f.read(wal.WAL_HEADER_SZ))
        assert hdr is not None
        pgnos = {frame[1] for frame in wal._iter_frames(f, hdr)
                 if frame[0] <= last_commit_frame}
    return sorted(pgnos)


def test_live_wal_recovery_applies_committed_frames(account, passphrase, workdir):
    shot = snap.snapshot(account.msg_db, workdir / "snap",
                         validator=snap.make_validator(passphrase))
    if shot.wal_path is None:
        pytest.skip("本次没有可验证的 WAL 更新（没有 -wal 文件）")
    plan = wal.inspect_wal(shot.wal_path, shot.shm_path)
    print(f"\nsnapshot consistency={shot.consistency} attempts={shot.attempts} "
          f"wal valid={plan.valid} reason={plan.reason} frames={plan.frame_count} "
          f"last_commit={plan.last_commit_frame} commit_size={plan.commit_size}")
    if not plan.valid:
        pytest.skip(f"本次没有可验证的 WAL 更新（inspect: {plan.reason}）")
    if plan.last_commit_frame == 0:
        pytest.skip("本次没有可验证的 WAL 更新（没有已提交帧）")

    params = sc.detect_params(passphrase, sc.read_header_and_page1(shot.db_path))
    assert params is not None
    plain_with = workdir / "with-wal.db"
    plain_without = workdir / "without-wal.db"
    dec.decrypt_pages(shot.db_path, plain_with, params)
    dec.decrypt_pages(shot.db_path, plain_without, params)
    applied = wal.apply_wal(params, shot.wal_path, plain_with, plan)
    print(f"applied frames={applied.frames_applied} "
          f"truncated_to={applied.truncated_to_pages}")

    with_data = plain_with.read_bytes()
    without_data = plain_without.read_bytes()
    pgnos = _committed_pgnos(shot.wal_path, plan.last_commit_frame)
    assert pgnos, "已提交帧里没有页号，WAL 结构异常"
    diffs = []
    for pgno in pgnos:
        off = (pgno - 1) * sc.PAGE_SZ
        if off + sc.PAGE_SZ > len(without_data):
            diffs.append(pgno)
            continue
        if with_data[off:off + sc.PAGE_SZ] != without_data[off:off + sc.PAGE_SZ]:
            diffs.append(pgno)
    if not diffs:
        pytest.skip("所有已提交帧的页内容都与主库相同，无法据此证明回放生效")
    assert len(with_data) == plan.commit_size * sc.PAGE_SZ, (
        f"含 WAL 产物页数 {len(with_data)//sc.PAGE_SZ} != commit_size "
        f"{plan.commit_size}")
    print(f"WAL 回放生效：{len(diffs)}/{len(pgnos)} 个已提交页与主库不同，"
          f"产物 {plan.commit_size} 页")


# ---------- 确定性（同一快照） ----------

def test_live_decrypt_is_deterministic(account, passphrase, workdir):
    shot = snap.snapshot(account.msg_db, workdir / "snap",
                         validator=snap.make_validator(passphrase))
    params = sc.detect_params(passphrase, sc.read_header_and_page1(shot.db_path))
    assert params is not None
    a = workdir / "a.db"
    b = workdir / "b.db"
    dec.decrypt_pages(shot.db_path, a, params)
    dec.decrypt_pages(shot.db_path, b, params)
    assert _sha256_stream(a) == _sha256_stream(b), "同一快照两次解密结果不一致"


# ---------- 只读性（分级 §12.7） ----------

def test_live_source_directory_is_never_modified(account, passphrase, workdir):
    before = _source_fingerprint(account)
    time.sleep(0.05)
    dec.decrypt_database(account, passphrase, dst_dir=workdir / "work")
    after = _source_fingerprint(account)

    removed = sorted(set(before) - set(after))
    assert not removed, f"源目录中有文件被删除：{removed[:5]}"
    changed = sorted(k for k in before if k in after and before[k] != after[k])
    added = sorted(set(after) - set(before))
    if changed or added:
        pytest.skip(f"QQ 运行中自身改写了 {changed[:5]}、新增了 {added[:5]}；"
                    "请完全退出 QQ 后重跑本项才能判定只读性")
    print("\n源目录 identity 三元组全部未变（只读性 PASS）")


# ---------- 流式大文件 ----------

def test_live_large_file_helpers_are_streaming(workdir):
    p = workdir / "big.bin"
    with open(p, "wb") as f:
        f.truncate(200 * 1024 * 1024)
    tracemalloc.start()
    try:
        _read_magic(p)
        _sha256_stream(p)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 8 * 1024 * 1024, f"峰值内存 {peak} 过高，疑似整文件载入"


# ---------- doctor ----------

def test_live_doctor_reports_no_failure(account, passphrase, workdir):
    # 多账号时 doctor 必须显式指定账号（不静默取第一个）
    rep = doctor.run_doctor(uin=account.uin, passphrase=passphrase,
                            keep_output=False, work_dir=workdir / "doctor")
    text = rep.to_text(ascii_only=True)
    print("\n" + text)
    assert passphrase.decode() not in text
    assert rep.ok, [(c.index, c.status, c.detail) for c in rep.failed]
    assert list((workdir / "doctor").rglob("*.decrypted.db")) == []
