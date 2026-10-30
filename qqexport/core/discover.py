"""定位 QQ NT 安装位置、数据目录与账号。

本机「文档」已知文件夹可能被重定向到其他盘（spec §3 实测为 D:\\Users\\<user>\\Documents），
所以主路径必须走 SHGetKnownFolderPath，%USERPROFILE%\\Documents 只能作最后回退。

隐私红线（修正案 A §0.5 / §2.2）：异常消息不得回显完整用户路径或真实 uin，
只用 <Documents> 之类占位符与 mask_uin() 掩码。
"""
from __future__ import annotations

import ctypes
import os
import re
import string
from dataclasses import dataclass
from pathlib import Path

_UIN_RE = re.compile(r"^\d{5,12}$")

MAX_DRIVE_SCAN_DEPTH = 5
MAX_VISITED_DIRS = 20000
_PRUNE = frozenset({
    "System Volume Information", "$RECYCLE.BIN", "$WinREAgent", "Recovery",
    "Windows", "ProgramData", "node_modules", ".git",
})


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]


# {FDD39AD0-238F-46AF-ADB4-6C85480369C7}
FOLDERID_Documents = _GUID(
    0xFDD39AD0, 0x238F, 0x46AF,
    (0xAD, 0xB4, 0x6C, 0x85, 0x48, 0x03, 0x69, 0xC7),
)


class DiscoveryError(RuntimeError):
    """发现阶段错误基类。消息只含占位符与掩码 uin，不含真实路径。"""


class NoAccountFound(DiscoveryError):
    """一个账号数据目录都没找到。消息必须带人工排查建议。"""


class DatabaseMissing(DiscoveryError):
    """账号目录在，但 nt_msg.db 缺失。"""


class DataDirPermissionDenied(DiscoveryError):
    """候选数据目录存在但无读取权限。"""


class ScanAborted(DiscoveryError):
    """扫盘超过目录上限（或超时）而中止，且没有找到任何账号。"""


class AmbiguousAccountError(DiscoveryError):
    """存在多个账号且调用方未显式指定；拒绝静默取第一个（修正案 A §2.3）。"""


@dataclass(frozen=True)
class QQAccount:
    uin: str
    data_dir: Path      # ...\Tencent Files\<uin>\nt_qq
    db_dir: Path        # <data_dir>\nt_db
    media_dir: Path     # <data_dir>\nt_data

    def __post_init__(self) -> None:
        # 修正案 A §2.5：路径合理性检查，不满足直接拒绝构造，不纳入结果。
        if (self.db_dir != self.data_dir / "nt_db"
                or self.media_dir != self.data_dir / "nt_data"):
            raise ValueError(
                "QQAccount 路径不一致：db_dir 必须是 data_dir/nt_db，"
                "media_dir 必须是 data_dir/nt_data")

    @property
    def msg_db(self) -> Path:
        return self.db_dir / "nt_msg.db"


def mask_uin(uin: str) -> str:
    """写入报告/异常消息用的 uin 掩码：只保留后 4 位（修正案 A §2.3）。"""
    return "****" + uin[-4:] if len(uin) >= 4 else "****"


def documents_folder() -> Path | None:
    """SHGetKnownFolderPath(FOLDERID_Documents)。失败返回 None 由调用方回退。"""
    if os.name != "nt":
        return None
    try:
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        ole32 = ctypes.WinDLL("ole32", use_last_error=True)
    except OSError:
        return None
    shell32.SHGetKnownFolderPath.argtypes = [
        ctypes.POINTER(_GUID), ctypes.c_ulong, ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_wchar_p),
    ]
    shell32.SHGetKnownFolderPath.restype = ctypes.c_long
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    out = ctypes.c_wchar_p()
    try:
        try:
            hr = shell32.SHGetKnownFolderPath(
                ctypes.byref(FOLDERID_Documents), 0, None, ctypes.byref(out))
        except OSError:
            return None
        if hr != 0 or not out.value:
            return None
        return Path(out.value)
    finally:
        # 所有路径（含错误路径）都必须释放，CoTaskMemFree(NULL) 是安全 no-op
        ole32.CoTaskMemFree(out)


def _registry_personal(subkey: str) -> str | None:
    """读 HKCU\\<subkey> 的 Personal 值并展开环境变量。失败返回 None。"""
    if os.name != "nt":
        return None
    try:
        import winreg
    except ImportError:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey) as k:
            val, _ = winreg.QueryValueEx(k, "Personal")
    except OSError:
        return None
    if not isinstance(val, str) or not val:
        return None
    return os.path.expandvars(val)


_SHELL_FOLDERS_KEYS = (
    r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
    r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders",
)


def candidate_roots(explicit: Path | None = None) -> list[Path]:
    """按 spec §6.1 的优先级产出候选 'Tencent Files' 根目录，大小写不敏感去重。"""
    raw: list[Path] = []
    if explicit is not None:
        raw.append(Path(explicit))
    docs = documents_folder()
    if docs is not None:
        raw.append(docs / "Tencent Files")
    for key in _SHELL_FOLDERS_KEYS:
        p = _registry_personal(key)
        if p:
            raw.append(Path(p) / "Tencent Files")
    profile = os.environ.get("USERPROFILE")
    if profile:
        raw.append(Path(profile) / "Documents" / "Tencent Files")

    seen: set[str] = set()
    out: list[Path] = []
    for r in raw:
        k = str(r).lower()
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


def _iter_accounts_under(root: Path) -> tuple[list[QQAccount], bool, list[str]]:
    """枚举 root 下的账号目录。

    返回 (账号列表, 该根目录是否被拒绝访问, uin 形状但缺 nt_msg.db 的目录名)。
    任何单条目上的 OSError / ValueError 都只跳过该条目，继续扫描。
    """
    accounts: list[QQAccount] = []
    missing_db: list[str] = []
    try:
        if not root.is_dir():
            return accounts, False, missing_db
    except OSError:
        return accounts, False, missing_db
    try:
        entries = list(os.scandir(root))
    except PermissionError:
        return accounts, True, missing_db
    except OSError:
        return accounts, False, missing_db
    for e in entries:
        try:
            # 修正案 A §2.4：绝不跟随符号链接 / junction
            if not e.is_dir(follow_symlinks=False) or not _UIN_RE.match(e.name):
                continue
            data = Path(e.path) / "nt_qq"
            db = data / "nt_db"
            if not (db / "nt_msg.db").is_file():
                missing_db.append(e.name)
                continue
            accounts.append(QQAccount(uin=e.name, data_dir=data, db_dir=db,
                                      media_dir=data / "nt_data"))
        except (OSError, ValueError):
            continue
    return accounts, False, missing_db


def _drive_roots() -> list[Path]:
    out: list[Path] = []
    for letter in string.ascii_uppercase:
        drive = Path(f"{letter}:\\")
        try:
            if drive.exists():
                out.append(drive)
        except OSError:
            continue
    return out


def find_tencent_files(
        max_depth: int = MAX_DRIVE_SCAN_DEPTH,
        max_dirs: int = MAX_VISITED_DIRS) -> tuple[list[Path], bool]:
    """有界深度优先搜索 'Tencent Files' 目录，逐个吞掉 OSError。

    返回 (命中目录列表, truncated)。truncated=True 表示达到 max_dirs 上限、
    仍有目录未访问（修正案 A §2.1）。
    """
    hits: list[Path] = []
    truncated = False
    visited = 0
    stack: list[tuple[str, int]] = [(str(r), 0) for r in _drive_roots()]
    while stack:
        cur, depth = stack.pop()
        if depth > max_depth:
            continue
        if visited >= max_dirs:
            truncated = True
            break
        visited += 1
        try:
            with os.scandir(cur) as it:
                entries = list(it)
        except OSError:
            continue
        for e in entries:
            try:
                # 修正案 A §2.4：绝不跟随符号链接 / junction
                if not e.is_dir(follow_symlinks=False) or e.name in _PRUNE:
                    continue
            except OSError:
                continue
            if e.name == "Tencent Files":
                hits.append(Path(e.path))
            else:
                stack.append((e.path, depth + 1))
    return hits, truncated


def discover_accounts(explicit: Path | None = None,
                      scan_drives: bool = False) -> list[QQAccount]:
    """发现所有可用账号，按 uin 升序（spec §6.1：按优先级尝试，取第一个命中者）。

    找不到时按实际情况抛 DiscoveryError 的对应子类；消息只含占位符与掩码 uin。
    scan_drives=True 才启用兜底递归扫盘（修正案 A §2.1：显式 opt-in）。
    """
    denied = False
    missing_db: list[str] = []
    for root in candidate_roots(explicit):
        found, root_denied, root_missing = _iter_accounts_under(root)
        denied = denied or root_denied
        missing_db.extend(root_missing)
        if found:
            return sorted(found, key=lambda a: a.uin)

    if scan_drives:
        roots, truncated = find_tencent_files()
        accounts: dict[str, QQAccount] = {}
        for root in roots:
            found, root_denied, root_missing = _iter_accounts_under(root)
            denied = denied or root_denied
            missing_db.extend(root_missing)
            for a in found:
                accounts.setdefault(a.uin, a)
        if accounts:
            return [accounts[k] for k in sorted(accounts)]
        if truncated:
            raise ScanAborted(
                f"扫盘已访问目录数达到上限（MAX_VISITED_DIRS={MAX_VISITED_DIRS}）"
                "而中止，且未找到任何账号。请用 --data-dir 直接指定 "
                "'Tencent Files' 目录。")

    if denied:
        raise DataDirPermissionDenied(
            "候选数据目录均无法读取（权限被拒）。请检查目录读取权限，"
            "或用 --data-dir 指定可访问的 'Tencent Files' 目录。")
    if missing_db:
        masked = "、".join(sorted({mask_uin(u) for u in missing_db}))
        raise DatabaseMissing(
            f"发现账号目录（候选：{masked}），但其下缺少 "
            "nt_qq\\nt_db\\nt_msg.db。QQ 可能从未成功登录过该账号，"
            "或数据目录不完整。")
    raise NoAccountFound(
        "未找到任何 QQ NT 账号数据目录。已按优先级尝试：--data-dir 显式目录、"
        "<Documents>\\Tencent Files（SHGetKnownFolderPath）、注册表 Personal、"
        "%USERPROFILE%\\Documents\\Tencent Files。请确认 QQ 已至少登录过一次，"
        "或用 --data-dir 手动指定 'Tencent Files' 目录（其下应有 "
        "<数字QQ号>\\nt_qq\\nt_db\\nt_msg.db）。")


def _activity_score(account: QQAccount) -> float:
    """当前登录账号通常有最新的 WAL/主库写入时间。"""
    best = 0.0
    for name in ("nt_msg.db-wal", "nt_msg.db", "nt_msg.db-shm"):
        p = account.db_dir / name
        try:
            if p.is_file():
                best = max(best, p.stat().st_mtime)
        except OSError:
            continue
    return best


def detect_active_account(accounts: list[QQAccount], *,
                          min_gap_s: float = 1.0) -> QQAccount:
    """按最近写入时间智能识别当前登录的 QQ 号。

    两个账号活跃度相同（或都没有写入记录）时不猜，抛 AmbiguousAccountError，
    由调用方回退到显式 uin/配置。
    """
    if not accounts:
        raise NoAccountFound("没有可识别的 QQ 账号数据目录。")
    if len(accounts) == 1:
        return accounts[0]
    ranked = sorted(accounts, key=_activity_score, reverse=True)
    top, second = ranked[0], ranked[1]
    if (_activity_score(top) <= 0
            or _activity_score(top) - _activity_score(second) <= min_gap_s):
        raise AmbiguousAccountError(
            "无法唯一识别当前登录的 QQ 号（多个账号近期都有写入）。候选："
            + _format_candidates(accounts) + "。请显式指定 uin。")
    return top


def select_account(accounts: list[QQAccount],
                   requested_uin: str | None) -> QQAccount:
    """唯一确定则返回；否则抛 AmbiguousAccountError（修正案 A §2.3）。

    消息只列候选的序号与 uin 后 4 位掩码，不回显完整 uin。
    """
    if not accounts:
        raise NoAccountFound(
            "没有可选择的账号：未发现任何账号数据目录。"
            "请确认 QQ 已登录过，或用 --data-dir 指定 'Tencent Files' 目录。")
    if requested_uin is not None:
        for a in accounts:
            if a.uin == requested_uin:
                return a
        raise AmbiguousAccountError(
            f"指定的账号 {mask_uin(requested_uin)} 不在已发现的账号中。候选："
            + _format_candidates(accounts)
            + "。请核对后显式指定 uin。")
    if len(accounts) == 1:
        return accounts[0]
    raise AmbiguousAccountError(
        "发现多个 QQ 账号，拒绝静默选择。候选："
        + _format_candidates(accounts)
        + "。请显式指定要使用的账号（uin）。")


def _format_candidates(accounts: list[QQAccount]) -> str:
    return "；".join(f"[{i}] {mask_uin(a.uin)}"
                     for i, a in enumerate(accounts, 1))


def _qqnt_bases() -> list[Path]:
    return [
        Path(r"C:\Program Files\Tencent\QQNT"),
        Path(r"C:\Program Files (x86)\Tencent\QQNT"),
    ]


def _version_key(name: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", name))


def qq_install_version() -> str | None:
    """返回 versions\\ 下最高版本号目录名，如 '9.9.36-53644'。找不到返回 None。"""
    for base in _qqnt_bases():
        vdir = base / "versions"
        try:
            if not vdir.is_dir():
                continue
            with os.scandir(vdir) as it:
                entries = list(it)
        except OSError:
            continue
        names: list[str] = []
        for e in entries:
            try:
                if e.is_dir(follow_symlinks=False):
                    names.append(e.name)
            except OSError:
                continue
        if names:
            return max(names, key=_version_key)
    return None
