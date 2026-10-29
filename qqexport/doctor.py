"""11 项环境自检（spec §9.3 的 P0 部分，修正案 A §9）。

每一项都包在 `_run()` 里：异常只留**类名**进 detail，绝不让 doctor 自己崩掉，
也绝不把异常文本（可能含路径或口令片段）带出去。捕获 `Exception` 而非
`BaseException`——用户按 Ctrl+C 必须向外传播（修正案 A §9.6）。

规则源：`CHECK_SPECS` 是实现与测试共用的唯一检查表（§9.1）；检查名从它派生，
顺序与编号不得随意改动（12–15 项属 P1+，本 task 不实现）。

默认只读、不留数据（§9.2）：解密探测默认使用 `TemporaryDirectory()`，
在 finally 中清理；`keep_output=True` 时必须显式给 `work_dir`，否则拒绝，
以免把明文库留在临时目录里。容量预估并入第 9 项（§9.3），按实际文件大小算。

脱敏（§9.5）：路径只输出 `<Documents>\\Tencent Files\\****<后4位>\\...` 形式，
uin 一律掩码；JSON 带 `schema_version`。报告 detail 里不出现完整用户路径。

`scan_drives=True` 时直接调用 `discover.find_tencent_files` 拿 `(hits, truncated)`，
把截断事实写进检查 3 的 detail（§9.7）。默认路径不触发盘符递归扫描。
"""
from __future__ import annotations

import contextlib
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import config
from .core import decrypt as dec
from .core import discover as disc
from .core import keyscan
from .core import keystore
from .core import sqlcipher as sc

EXPECTED_TABLES = ("c2c_msg_table", "group_msg_table")
EXPECTED_COLUMNS = ("40001", "40011", "40012", "40013", "40020", "40021",
                    "40030", "40033", "40050", "40800")

_N_CHECKS = 11
_UIN_RE = re.compile(r"^\d{5,12}$")


@dataclass(frozen=True)
class CheckSpec:
    index: int
    name: str
    inputs: str
    pass_when: str
    warn_when: str
    fail_when: str
    side_effects: str
    skips_on_fail: bool


CHECK_SPECS: tuple[CheckSpec, ...] = (
    CheckSpec(1, "Python 与 cryptography", "sys, cryptography",
              "均可导入", "—", "导入失败", "无", True),
    CheckSpec(2, "QQ 安装与版本", "QQNT versions 目录",
              "找到版本号", "未找到安装", "读取异常", "无", True),
    CheckSpec(3, "数据目录发现", "discover（默认不扫盘）",
              "至少一个账号", "盘符扫描被截断", "未找到/权限拒绝", "只读", True),
    CheckSpec(4, "账号枚举", "账号列表 + --uin",
              "选定账号存在", "—", "多账号未指定/uin 不存在", "无", True),
    CheckSpec(5, "nt_msg.db 结构校验", "nt_msg.db 尺寸与头形状",
              "size % 4096 == 1024", "—", "缺失/形状不符", "只读", True),
    CheckSpec(6, "QQ.exe 运行状态", "进程枚举 + 口令可用性",
              "QQ 在运行", "未运行但有口令/缓存", "未运行且无口令", "只读", True),
    CheckSpec(7, "数据库口令", "调用方口令 / 缓存 / --scan",
              "page-1 HMAC 通过", "—", "无口令/扫描失败/口令错", "只读*", True),
    CheckSpec(8, "加密参数探测", "八组合 page-1 HMAC",
              "命中组合", "—", "全部不通过", "只读", True),
    CheckSpec(9, "解密与页 1 魔数", "容量预估 + decrypt_database",
              "complete 且页 1 有魔数", "lossy / 空间不足", "failed", "写临时明文", True),
    CheckSpec(10, "WAL 恢复", "DecryptResult.wal_*",
              "WAL 完整回放", "WAL 缺失/无效/不完整", "—", "无", False),
    CheckSpec(11, "目标表与关键列", "DecryptResult.tables",
              "期望表存在", "表缺失", "—", "无", False),
)

_CHECK_NAMES = tuple(s.name for s in CHECK_SPECS)


# ---- 可 monkeypatch 的边界 ----

def _discover(**kwargs):
    return disc.discover_accounts(**kwargs)


def _qq_version():
    return disc.qq_install_version()


def _qq_pids():
    return keyscan.find_qq_pids()


def _key_store():
    return keystore.KeyStore()


def _now() -> str:
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


# ---- 渲染与脱敏 ----

def _redact_path(path: Path) -> str:
    """把数据目录渲染成 `<Documents>\\Tencent Files\\****<后4位>\\...`（§9.5）。

    以 uin 目录为锚点，把它之前的所有层级（盘符、用户名、已知文件夹的真实位置）
    全部折叠成占位符——不能依赖目录名一定叫 "Tencent Files"。
    """
    parts = list(Path(path).parts)
    uin_idx = next((i for i, part in enumerate(parts) if _UIN_RE.match(part)), None)
    if uin_idx is None:
        return "<path>"
    tail = [disc.mask_uin(parts[uin_idx]), *parts[uin_idx + 1:]]
    return "\\".join(["<Documents>", "Tencent Files", *tail])


def _run(index: int, name: str, fn):
    """执行一项检查，返回 (CheckResult, 产出值)。

    fn 返回 (detail, value)；detail 以 `WARN:` / `FAIL:` 开头表示非 ok 状态。
    异常只留类名，绝不带异常文本或 traceback。不得捕获 BaseException。
    """
    from .report import CheckResult
    try:
        detail, value = fn()
    except Exception as exc:                     # noqa: BLE001 —— 诊断工具必须自持
        return CheckResult(index, name, "fail", f"{exc.__class__.__name__}"), None
    if detail.startswith("WARN:"):
        return CheckResult(index, name, "warn", detail[5:].strip()), value
    if detail.startswith("FAIL:"):
        return CheckResult(index, name, "fail", detail[5:].strip()), None
    return CheckResult(index, name, "ok", detail), value


def _skip_rest(results: list) -> list:
    """把尚未执行的检查补成 skip，保证报告恒为 11 项。"""
    from .report import CheckResult
    return [CheckResult(i, _CHECK_NAMES[i - 1], "skip", "前置检查未通过")
            for i in range(len(results) + 1, _N_CHECKS + 1)]


def _downgrade_reasons(res) -> list[str]:
    reasons: list[str] = []
    if res.corrupt_pages:
        reasons.append(f"{res.corrupt_pages} 个页损坏")
    if res.wal_reason not in ("ok", "wal_absent", ""):
        reasons.append(f"WAL 无效（{res.wal_reason}）")
    if res.wal_signature_changed:
        reasons.append("WAL 在探测与回放之间被改写")
    if res.wal_stopped_early:
        reasons.append("WAL 提前停止回放")
    if res.wal_corrupt_frames:
        reasons.append(f"{res.wal_corrupt_frames} 个 WAL 帧损坏")
    if res.snapshot_consistency == "unverified":
        reasons.append("快照一致性未验证")
    if res.integrity_check != "ok":
        reasons.append(f"quick_check={res.integrity_check}")
    if res.status == "failed" and not reasons:
        reasons.append("产物未通过完整性自检")
    return reasons


def _has_cached_key(uin: str) -> bool:
    try:
        return any(e.uin == uin and not e.corrupt
                   for e in _key_store().entries())
    except Exception:                            # noqa: BLE001 —— 缓存损坏按无缓存处理
        return False


def _remove_plaintext(directory: Path) -> None:
    for pattern in ("*.decrypted.db", "*.partial"):
        for p in directory.glob(pattern):
            with contextlib.suppress(OSError):
                p.unlink()


def run_doctor(*, data_dir: Path | None = None, uin: str | None = None,
               passphrase: bytes | None = None, allow_scan: bool = False,
               scan_timeout_s: float = 120.0, work_dir: Path | None = None,
               keep_output: bool = False,
               scan_drives: bool = False):
    from .report import DoctorReport

    if keep_output and work_dir is None:
        raise ValueError("keep_output=True 需要显式 work_dir，避免明文落在临时目录")

    results: list = []
    tempdir = None
    probe_dir: Path | None = None
    try:
        if work_dir is None:
            tempdir = tempfile.TemporaryDirectory(prefix="qqexport-doctor-")
            probe_dir = Path(tempdir.name)
        else:
            probe_dir = Path(work_dir)
            probe_dir.mkdir(parents=True, exist_ok=True)

        def add(res, value=None):
            results.append(res)
            return value

        # 1. Python 与 cryptography
        def c1():
            import cryptography
            return (f"Python {sys.version.split()[0]}, cryptography "
                    f"{getattr(cryptography, '__version__', 'unknown')}", None)
        add(*_run(1, _CHECK_NAMES[0], c1))

        # 2. QQ 安装与版本
        def c2():
            ver = _qq_version()
            if ver is None:
                return ("WARN:未找到 QQNT 安装（不影响解密，仅用于版本漂移诊断）",
                        None)
            return ver, ver
        add(*_run(2, _CHECK_NAMES[1], c2))

        # 3. 数据目录发现（默认不扫盘；scan_drives=True 时如实报告截断）
        def c3():
            truncated = False
            if scan_drives:
                _hits, truncated = disc.find_tencent_files()
            found = _discover(explicit=data_dir, scan_drives=scan_drives)
            hit = _redact_path(found[0].data_dir)
            detail = f"{len(found)} 个账号；命中 {hit}"
            if truncated:
                return ("WARN:" + detail +
                        f"；盘符扫描在 {disc.MAX_VISITED_DIRS} 个目录处达到上限"
                        "而中止，可能未列出全部账号", found)
            return detail, found
        accounts = add(*_run(3, _CHECK_NAMES[2], c3))
        if results[-1].status == "fail":
            return _report(results, _skip_rest(results))

        # 4. 账号枚举（多账号必须显式指定，不静默取第一个）
        def c4():
            try:
                acct = disc.select_account(accounts, uin)
            except disc.AmbiguousAccountError:
                candidates = "；".join(
                    f"[{i}] {disc.mask_uin(a.uin)}"
                    for i, a in enumerate(accounts, 1))
                return ("FAIL:发现多个账号，拒绝静默选择；请用 --uin 指定。候选："
                        + candidates, None)
            return f"已选定账号 {disc.mask_uin(acct.uin)}", acct
        account = add(*_run(4, _CHECK_NAMES[3], c4))
        if account is None:
            return _report(results, _skip_rest(results))

        # 5. nt_msg.db 结构校验
        def c5():
            db = account.msg_db
            if not db.is_file():
                raise FileNotFoundError("nt_msg.db 不存在")
            size = db.stat().st_size
            if not sc.has_wrapper_header(size):
                raise ValueError(f"size {size} 不满足 %4096==1024，可能不是 QQ NT 库")
            return (f"{size:,} B = {sc.page_count_from_size(size):,} 页 × 4096 + 1024",
                    size)
        add(*_run(5, _CHECK_NAMES[4], c5))
        if results[-1].status == "fail":
            return _report(results, _skip_rest(results))

        # 6. QQ.exe 运行状态（未运行但有口令/缓存 → warn；都没有 → fail）
        def c6():
            pids = _qq_pids()
            if pids:
                return f"{len(pids)} 个进程", pids
            if passphrase is not None or _has_cached_key(account.uin):
                return ("WARN:QQ.exe 未运行；将使用已提供的口令或缓存继续", [])
            return ("FAIL:QQ.exe 未运行，且没有可用口令（无缓存、未提供）。"
                    "请先启动并登录 QQ，或运行 `qqexport key scan` / "
                    "`qqexport key set`", None)
        add(*_run(6, _CHECK_NAMES[5], c6))
        if results[-1].status == "fail":
            return _report(results, _skip_rest(results))

        # 7. 数据库口令（必须真的验证 page-1 HMAC，不得「给了就算通过」）
        def c7():
            head = sc.read_header_and_page1(account.msg_db)
            cand, source = passphrase, "调用方提供"
            if cand is None:
                cand = _key_store().load(account.uin)
                source = "命中缓存"
            if cand is None:
                if not allow_scan:
                    return ("FAIL:无口令且未允许内存扫描。请加 --scan，"
                            "或先用 `qqexport key set` 写入", None)
                try:
                    cand = keyscan.scan_key(
                        lambda c: sc.detect_params(c, head) is not None,
                        timeout_s=scan_timeout_s)
                except keyscan.ProcessAccessDenied:
                    return ("FAIL:读取 QQ 进程内存被系统拒绝；"
                            "请以管理员身份重试，或改用 key set", None)
                source = "内存扫描成功"
                if cand is None:
                    return "FAIL:内存扫描未找到可用口令", None
            if sc.detect_params(cand, head) is None:
                return ("FAIL:口令无法认证该库的第 1 页——口令不对，"
                        "或 QQ 版本改动了加密参数", None)
            return f"{source}，指纹 {keystore.fingerprint(cand)}", cand
        got = add(*_run(7, _CHECK_NAMES[6], c7))
        if got is None:
            return _report(results, _skip_rest(results))

        # 8. 加密参数探测
        def c8():
            head = sc.read_header_and_page1(account.msg_db)
            params = sc.detect_params(got, head)
            if params is None:
                return "FAIL:八组合均未通过 page-1 HMAC 校验", None
            return params.fingerprint, params
        params = add(*_run(8, _CHECK_NAMES[7], c8))
        if params is None:
            return _report(results, _skip_rest(results))

        # 9. 容量预估 + 解密与页 1 魔数
        def c9():
            db_size = account.msg_db.stat().st_size
            wal = account.msg_db.with_name(account.msg_db.name + "-wal")
            wal_size = wal.stat().st_size if wal.is_file() else 0
            need = (db_size + wal_size) * 3
            try:
                free = shutil.disk_usage(probe_dir).free
            except OSError:
                return "WARN:无法读取目标卷可用空间，跳过容量预估", None
            if need > free:
                return (f"WARN:工作空间可能不足：需要 {need:,} B，"
                        f"可用 {free:,} B", None)
            res = dec.decrypt_database(account, got, dst_dir=probe_dir)
            reasons = _downgrade_reasons(res)
            if res.status == "failed":
                return ("FAIL:口令正确但产物不可用："
                        + ("；".join(reasons) or "未通过完整性自检"), None)
            detail = (f"{res.page_count:,} 页，损坏 {res.corrupt_pages}，"
                      f"quick_check={res.integrity_check}，status={res.status}")
            if res.status == "lossy":
                return "WARN:" + detail + "；降级原因：" + "；".join(reasons), res
            return detail, res
        result = add(*_run(9, _CHECK_NAMES[8], c9))
        if result is None:
            return _report(results, _skip_rest(results))

        # 10. WAL 恢复
        def c10():
            if result.wal_reason == "wal_absent":
                return "WARN:无 -wal 文件，本次产物只含已 checkpoint 的数据", None
            detail = (f"{result.wal_frames} 帧，最后 commit 在第 "
                      f"{result.wal_last_commit_frame} 帧，commit 后 "
                      f"{result.wal_commit_size} 页，已应用 "
                      f"{result.wal_applied_frames} 帧，shm 交叉验证="
                      f"{result.shm_validated}")
            if (result.wal_corrupt_frames or result.wal_signature_changed
                    or result.wal_stopped_early
                    or result.wal_reason not in ("ok", "wal_absent", "")):
                return "WARN:" + detail, None
            return detail, None
        add(*_run(10, _CHECK_NAMES[9], c10))

        # 11. 目标表与关键列
        def c11():
            found = set(result.tables)
            missing = [t for t in EXPECTED_TABLES if t not in found]
            detail = (f"共 {len(found)} 张表；期望表 "
                      f"{','.join(EXPECTED_TABLES)}；缺失 {missing or '无'}")
            if missing:
                return "WARN:" + detail, None
            return detail, None
        add(*_run(11, _CHECK_NAMES[10], c11))

        return _report(results, [])
    finally:
        if tempdir is not None:
            tempdir.cleanup()
        elif probe_dir is not None and not keep_output:
            _remove_plaintext(probe_dir)


def _report(results: list, extra: list):
    from .report import DoctorReport
    results = results + extra
    return DoctorReport(checks=results, generated_at=_now())
