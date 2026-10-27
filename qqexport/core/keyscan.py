"""被动内存扫描取得 QQ NT 的 SQLCipher 口令。参照 spec §6.2、修正案 A §6。

**只读。** 只使用 OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION)、
VirtualQueryEx、ReadProcessMemory、CloseHandle。不注入 DLL、不附加调试器、
不申请任何写权限、不修改 QQ 的任何文件（spec §12 R7）。

不变量：
- 纯计算与 Win32 调用彻底分离。`find_key_candidates` 与 `scan_with_reader`
  不含系统调用；`Win32ProcessReader` 只负责把 VirtualQueryEx / ReadProcessMemory
  翻译成 `MemoryReader` 协议。测试注入假 reader / 假 kernel32，真机验证留给 live 冒烟。
- 候选形状：长度恰为 32 或 16 的可打印 ASCII，且前为 NUL 或区域起始、后为 NUL
  —— NTQQ 以 C 字符串形式把口令传给 nt_sqlite3_key_v2。32 必须排在前面的交替项，
  否则 32 字节串会被切成两个 16 字节候选。
- 判定完全交给调用方的 `verify`（拿**选定账号**真库 page-1 HMAC）。能通过就是
  该账号的密钥，属确定性判定而非启发式挑选（修正案 A §6.2、裁定 R15）。
  因此多进程扫描中任一进程命中即可返回。
- 失败语义（裁定 R27）：区域级读取失败返回 None 并跳过；进程级失败跳过该进程；
  只有「没有任何进程产出密钥且至少一个进程权限被拒」时才抛 ProcessAccessDenied。
  计划里的「read 失败即抛权限异常」会中止整个多进程扫描，与修正案 §6.3 冲突。
- 错误码 5 / 1314 是权限拒绝，其余静默跳过（spec §6.2）。异常消息不含 pid、
  路径、口令或原始 Win32 文本。
"""
from __future__ import annotations

import ctypes
import os
import re
import time
from ctypes import wintypes
from typing import Callable, Iterator, Protocol

# 只读权限位。参照 spec §6.2 / §12 R7，不得增加或改写。
PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400
MEM_COMMIT = 0x1000
# PAGE_READONLY / READWRITE / WRITECOPY / EXECUTE / EXECUTE_READ / EXECUTE_READWRITE
# / EXECUTE_WRITECOPY。spec §6.2 第 3 步的筛选集，照抄不改。
READABLE_PROTECTS = frozenset({0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80})
MAX_REGION_SIZE = 200 * 1024 * 1024
# 交替式最长优先：32 必须在 16 之前。
KEY_LENGTHS = (32, 16)
ACCESS_DENIED_WINERRORS = frozenset({5, 1314})
QQ_PROCESS_NAMES = ("QQ.exe", "QQEX.exe")
DEFAULT_TIMEOUT_S = 120.0
TH32CS_SNAPPROCESS = 0x00000002
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
# x64 用户地址空间上界。到顶仍无结果时正常结束枚举。
_MAX_USER_ADDRESS = 0x7FFFFFFFFFFF
_ADDRESS_STEP = 0x1000

_ALTERNATIVES = b"|".join([b"[\x20-\x7e]{%d}" % n for n in KEY_LENGTHS])
_CANDIDATE_RE = re.compile(rb"(?<=\x00)(" + _ALTERNATIVES + rb")(?=\x00)")


class ProcessAccessDenied(RuntimeError):
    """Win32 权限拒绝。异常消息刻意不含 pid、路径与原始错误文本。"""

    code = "process_access_denied"

    def __init__(self) -> None:
        super().__init__(self.code)


def find_key_candidates(blob: bytes) -> list[bytes]:
    """从一块内存中提取口令候选，去重且保持首次出现顺序。

    纯函数：不含任何系统调用，可穷举测试。给 blob 前置一个 NUL 字节，
    使「区域起始处」的候选也能满足 lookbehind（spec §6.2 第 4 步）。
    """
    if not blob:
        return []
    padded = b"\x00" + blob
    out: list[bytes] = []
    seen: set[bytes] = set()
    for m in _CANDIDATE_RE.finditer(padded):
        cand = m.group(1)
        if cand not in seen:
            seen.add(cand)
            out.append(cand)
    return out


class MemoryReader(Protocol):
    """区域遍历与读取的抽象。测试用假对象注入，Win32 实现见下。"""

    def regions(self) -> Iterator[tuple[int, int]]: ...
    def read(self, base: int, size: int) -> bytes | None: ...
    def close(self) -> None: ...


def scan_with_reader(reader: MemoryReader, verify: Callable[[bytes], bool], *,
                     timeout_s: float | None = DEFAULT_TIMEOUT_S,
                     clock: Callable[[], float] = time.monotonic) -> bytes | None:
    """遍历 reader 的区域，返回首个通过 verify 的候选。

    超时覆盖整个扫描过程（每次进入新区域前检查），不是单次 API 调用。
    跨区域去重：同一候选只交给 verify 一次（spec §6.2 第 5 步）。
    无论命中、超时还是异常，reader 都会被关闭。
    """
    deadline = None if timeout_s is None else clock() + timeout_s
    seen: set[bytes] = set()
    try:
        for base, size in reader.regions():
            if deadline is not None and clock() > deadline:
                return None
            if size <= 0 or size > MAX_REGION_SIZE:
                continue
            blob = reader.read(base, size)
            if not blob:
                continue
            for cand in find_key_candidates(blob):
                if cand in seen:
                    continue
                seen.add(cand)
                if verify(cand):
                    return cand
    finally:
        reader.close()
    return None


class _MBI64(ctypes.Structure):
    """MEMORY_BASIC_INFORMATION（x64 布局）。仅支持 64 位进程。"""

    _fields_ = [
        ("BaseAddress", ctypes.c_uint64),
        ("AllocationBase", ctypes.c_uint64),
        ("AllocationProtect", wintypes.DWORD),
        ("__alignment1", wintypes.DWORD),
        ("RegionSize", ctypes.c_uint64),
        ("State", wintypes.DWORD),
        ("Protect", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("__alignment2", wintypes.DWORD),
    ]


def _default_kernel32():
    dll = ctypes.WinDLL("kernel32", use_last_error=True)
    dll.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    dll.OpenProcess.restype = wintypes.HANDLE
    dll.VirtualQueryEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p,
                                   ctypes.c_void_p, ctypes.c_size_t]
    dll.VirtualQueryEx.restype = ctypes.c_size_t
    dll.ReadProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p,
                                      ctypes.c_void_p, ctypes.c_size_t,
                                      ctypes.POINTER(ctypes.c_size_t)]
    dll.ReadProcessMemory.restype = wintypes.BOOL
    dll.CloseHandle.argtypes = [wintypes.HANDLE]
    dll.CloseHandle.restype = wintypes.BOOL
    return dll


class Win32ProcessReader:
    """把 VirtualQueryEx / ReadProcessMemory 翻译成 MemoryReader 协议。

    句柄惰性打开并缓存：全程只调用一次 OpenProcess，权限位固定为只读
    （修正案 A §6.4）。`kernel32` 可注入假对象，测试据此断言实际请求的权限。
    注入时用假对象自己的 GetLastError；否则用 ctypes 的线程局部副本
    （裁定 R28：计划的 `use_last_error` 探测在 Python 3.14 上恒为 False）。
    """

    def __init__(self, pid: int, kernel32=None) -> None:
        self._pid = pid
        if kernel32 is None:
            self._dll = _default_kernel32()
            self._last_error: Callable[[], int] = ctypes.get_last_error
        else:
            self._dll = kernel32
            self._last_error = kernel32.GetLastError
        self._handle = None

    def _open(self):
        if self._handle is not None:
            return self._handle
        handle = self._dll.OpenProcess(
            PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, self._pid)
        if not handle:
            err = int(self._last_error())
            if err in ACCESS_DENIED_WINERRORS:
                raise ProcessAccessDenied()
            raise OSError(f"OpenProcess 失败（Win32 错误码 {err}）")
        self._handle = handle
        return handle

    def regions(self) -> Iterator[tuple[int, int]]:
        handle = self._open()
        mbi = _MBI64()
        address = 0
        while address < _MAX_USER_ADDRESS:
            ret = self._dll.VirtualQueryEx(handle, ctypes.c_void_p(address),
                                           ctypes.byref(mbi),
                                           ctypes.sizeof(mbi))
            if not ret:
                err = int(self._last_error())
                if err in ACCESS_DENIED_WINERRORS:
                    raise ProcessAccessDenied()
                return
            base = int(mbi.BaseAddress)
            size = int(mbi.RegionSize)
            if size <= 0:
                address += _ADDRESS_STEP
                continue
            if int(mbi.State) == MEM_COMMIT and int(mbi.Protect) in READABLE_PROTECTS:
                yield base, size
            address = max(base + size, address + _ADDRESS_STEP)

    def read(self, base: int, size: int) -> bytes | None:
        """读取失败返回 None（区域已失效 / 进程退出），由调用方跳过。

        部分读取（read_n < size）时只返回实际读到的字节；即使 Windows 以
        ERROR_PARTIAL_COPY 返回失败，已读到的前缀也是可用的。
        """
        handle = self._open()
        buf = ctypes.create_string_buffer(size)
        read_n = ctypes.c_size_t(0)
        ok = self._dll.ReadProcessMemory(handle, ctypes.c_void_p(base), buf,
                                         size, ctypes.byref(read_n))
        n = int(read_n.value)
        if not ok:
            return bytes(buf.raw[:n]) if n else None
        return bytes(buf.raw[:n])

    def close(self) -> None:
        handle, self._handle = self._handle, None
        if handle is not None:
            self._dll.CloseHandle(handle)


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_void_p),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


def find_qq_pids(names: tuple[str, ...] = QQ_PROCESS_NAMES) -> list[int]:
    """枚举 QQ 相关进程的 PID。非 Windows 或枚举失败时返回空列表。"""
    if os.name != "nt":
        return []
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    except OSError:
        return []
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE,
                                         ctypes.POINTER(_PROCESSENTRY32W)]
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE,
                                        ctypes.POINTER(_PROCESSENTRY32W)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == _INVALID_HANDLE_VALUE:
        return []
    wanted = {n.lower() for n in names}
    pids: list[int] = []
    try:
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
        if not kernel32.Process32FirstW(snap, ctypes.byref(entry)):
            return []
        while True:
            if entry.szExeFile.lower() in wanted:
                pids.append(int(entry.th32ProcessID))
            if not kernel32.Process32NextW(snap, ctypes.byref(entry)):
                break
    finally:
        kernel32.CloseHandle(snap)
    return sorted(set(pids))


def scan_key(verify: Callable[[bytes], bool], *,
             timeout_s: float | None = DEFAULT_TIMEOUT_S) -> bytes | None:
    """遍历所有 QQ 进程做被动扫描，返回首个通过 verify 的候选。

    每次打开进程只用只读权限。单进程失败（进程已退出、权限被拒）跳过并继续；
    全部进程都没命中且至少一个被拒时抛 ProcessAccessDenied（裁定 R27）。
    扫描显式 opt-in：只有 `qqexport key scan` 与 `qqexport doctor --scan` 调用它
    （修正案 A §6.1）。
    """
    deadline = None if timeout_s is None else time.monotonic() + timeout_s
    denied = False
    for pid in find_qq_pids():
        if deadline is None:
            remain = None
        else:
            remain = deadline - time.monotonic()
            if remain <= 0:
                return None
        try:
            reader = Win32ProcessReader(pid)
            got = scan_with_reader(reader, verify, timeout_s=remain)
        except ProcessAccessDenied:
            denied = True
            continue
        except OSError:
            continue
        if got is not None:
            return got
    if denied:
        raise ProcessAccessDenied()
    return None
