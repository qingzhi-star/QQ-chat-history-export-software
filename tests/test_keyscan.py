"""keyscan 的单元测试。

鉴别力约定（HANDOFF §5.2）：每条测试都对应实现里一段可删除的逻辑，
删掉就该测试失败。全部使用合成 reader 与假 kernel32，不触碰真实 QQ 进程，
也不读真实进程内存（修正案 A §0.7：CI 不得读真实进程内存）。
"""
import inspect
from pathlib import Path

import pytest

from qqexport.core import keyscan as ks


def wrap(payload: bytes) -> bytes:
    """把候选串包成内存里常见的形态：前后都是 NUL。"""
    return b"\x00\x00" + payload + b"\x00\x00"


K16 = b"#8FakeKeyForTest"                      # 16 字节可打印 ASCII
K32 = b"A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6"      # 32 字节


# ---------- find_key_candidates（纯函数） ----------

def test_finds_16_byte_candidate():
    assert ks.find_key_candidates(wrap(K16)) == [K16]


def test_finds_32_byte_candidate():
    assert ks.find_key_candidates(wrap(K32)) == [K32]


def test_finds_candidate_at_blob_start():
    """区域起始处的候选：实现必须给 blob 前置 NUL 才能命中 lookbehind。"""
    assert ks.find_key_candidates(K16 + b"\x00") == [K16]


def test_finds_32_byte_candidate_at_blob_start():
    assert ks.find_key_candidates(K32 + b"\x00") == [K32]


@pytest.mark.parametrize("bad", [
    b"#8FakeKeyForTes",                  # 15
    b"#8FakeKeyForTestX",                # 17
    b"A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p",  # 31
    K32 + b"Q",                          # 33
])
def test_rejects_wrong_length_runs(bad):
    assert ks.find_key_candidates(wrap(bad)) == []


def test_rejects_non_printable_bytes_inside():
    assert ks.find_key_candidates(b"\x00#8Fake\x01KeyForTest\x00") == []


def test_requires_trailing_nul():
    """没有结束 NUL 就不是 C 字符串，不能当候选。"""
    assert ks.find_key_candidates(b"\x00" + K16 + b"\x41") == []


def test_48_byte_run_is_not_matched():
    """32 字节交替项会在偏移 16 处匹配，但其前一字节是可打印字符，必须被拒。

    删掉 lookbehind 后这条会失败（返回 K32 + b"0123456789abcdef" 的中间片段）。
    """
    assert ks.find_key_candidates(wrap(K32 + b"0123456789abcdef")) == []


def test_finds_multiple_and_dedupes_preserving_order():
    blob = wrap(K16) + b"\xff" * 40 + wrap(K32) + b"\xff" * 40 + wrap(K16)
    assert ks.find_key_candidates(blob) == [K16, K32]


def test_returns_bytes_not_str():
    assert all(isinstance(c, bytes) for c in ks.find_key_candidates(wrap(K16)))


def test_empty_blob():
    assert ks.find_key_candidates(b"") == []


def test_realistic_heap_noise():
    """夹在典型堆内容（指针、字符串表）中间也要能挑出来。"""
    noise = (b"\x7f\x00\x00\x01\x00\x00\x00\x00"
             + b"nt_sqlite3_key_v2: db=%p zDb=%s\x00"
             + b"\x10\x20\x30\x40" * 8)
    blob = noise + wrap(K16) + noise
    assert ks.find_key_candidates(blob) == [K16]


# ---------- scan_with_reader ----------

class FakeReader:
    """按脚本产出区域；可注入读取失败。"""

    def __init__(self, regions, reads):
        self._regions = regions
        self._reads = reads
        self.read_calls = []
        self.closed = False

    def regions(self):
        yield from self._regions

    def read(self, base, size):
        self.read_calls.append((base, size))
        return self._reads.get(base)

    def close(self):
        self.closed = True


def test_scan_returns_first_verifying_candidate():
    blob = wrap(b"#8NotTheKeyXXXXX") + b"\xff" * 8 + wrap(K16)
    reader = FakeReader([(0x1000, len(blob))], {0x1000: blob})
    assert ks.scan_with_reader(reader, lambda c: c == K16) == K16


def test_scan_returns_none_when_nothing_verifies():
    blob = wrap(K16)
    reader = FakeReader([(0x1000, len(blob))], {0x1000: blob})
    assert ks.scan_with_reader(reader, lambda c: False) is None


def test_scan_skips_oversized_regions():
    reader = FakeReader([(0x1000, ks.MAX_REGION_SIZE + 1)], {})
    assert ks.scan_with_reader(reader, lambda c: True) is None
    assert reader.read_calls == []


def test_scan_skips_zero_size_regions():
    reader = FakeReader([(0x1000, 0)], {})
    ks.scan_with_reader(reader, lambda c: True)
    assert reader.read_calls == []


def test_scan_tolerates_read_returning_none():
    """进程中途退出时 read 返回 None：跳过该区域继续，不崩溃。"""
    blob = wrap(K16)
    reader = FakeReader([(0x1000, 4096), (0x2000, len(blob))], {0x2000: blob})
    assert ks.scan_with_reader(reader, lambda c: c == K16) == K16


def test_scan_dedupes_candidate_across_regions():
    """同一候选在两个区域重复出现时，verify 只应被调用一次（spec §6.2 第 5 步）。

    删掉跨区域 seen 集合后 calls 会变成 [K16, K16]。
    """
    calls = []
    blob = wrap(K16)

    def verify(c):
        calls.append(c)
        return False

    reader = FakeReader([(0x1000, len(blob)), (0x2000, len(blob))],
                        {0x1000: blob, 0x2000: blob})
    assert ks.scan_with_reader(reader, verify, timeout_s=None) is None
    assert calls == [K16]


def test_scan_respects_timeout_with_injected_clock():
    """verify 恒为 False，否则第一个区域就命中返回，永远走不到超时分支。"""
    ticks = iter([0.0, 0.0, 500.0, 900.0, 1500.0])
    blob = wrap(K16)
    regions = [(i * 0x10000, len(blob)) for i in range(3)]
    reads = {b: blob for b, _ in regions}
    reader = FakeReader(regions, reads)
    got = ks.scan_with_reader(reader, lambda c: False,
                              timeout_s=120.0, clock=lambda: next(ticks))
    assert got is None
    assert len(reader.read_calls) == 1          # 第二个区域前就已超时


def test_scan_unbounded_when_timeout_is_none():
    blob = wrap(K16)
    regions = [(i * 0x10000, len(blob)) for i in range(5)]
    reads = {b: blob for b, _ in regions}
    reader = FakeReader(regions, reads)
    assert ks.scan_with_reader(reader, lambda c: False, timeout_s=None) is None
    assert len(reader.read_calls) == 5


def test_scan_stops_at_first_match_without_reading_rest():
    blob = wrap(K16)
    regions = [(0x1000, len(blob)), (0x2000, len(blob))]
    reader = FakeReader(regions, {0x1000: blob, 0x2000: blob})
    assert ks.scan_with_reader(reader, lambda c: c == K16) == K16
    assert reader.read_calls == [(0x1000, len(blob))]


def test_scan_closes_reader_even_when_verify_raises():
    """reader.close() 必须在 finally 里，异常路径也不能泄漏句柄。"""
    blob = wrap(K16)
    reader = FakeReader([(0x1000, len(blob))], {0x1000: blob})

    def boom(candidate):
        raise RuntimeError("verify 内部故障")

    with pytest.raises(RuntimeError):
        ks.scan_with_reader(reader, boom)
    assert reader.closed


# ---------- Win32 层（假 kernel32） ----------

class _FakeMbi:
    def __init__(self):
        self.BaseAddress = 0
        self.RegionSize = 0
        self.State = 0
        self.Protect = 0


class _FakeKernel32:
    """只实现被测代码用到的那几个调用，并记录请求过的权限与次数。"""

    def __init__(self, *, open_result=1, last_error=0, regions=(), reads=None):
        self._open_result = open_result
        self._last_error = last_error
        self._regions = list(regions)
        self._reads = reads or {}
        self.open_calls = []
        self.closed = []
        self._i = 0
        self.OpenProcess = self._open_process
        self.VirtualQueryEx = self._virtual_query_ex
        self.ReadProcessMemory = self._read_process_memory
        self.CloseHandle = self._close_handle
        self.GetLastError = lambda: self._last_error

    def _open_process(self, access, inherit, pid):
        self.open_calls.append((access, inherit, pid))
        return self._open_result

    def _virtual_query_ex(self, handle, addr, mbi_ref, size):
        if self._i >= len(self._regions):
            return 0
        base, region_size, state, protect = self._regions[self._i]
        self._i += 1
        mbi = mbi_ref._obj
        mbi.BaseAddress, mbi.RegionSize = base, region_size
        mbi.State, mbi.Protect = state, protect
        return 1

    def _read_process_memory(self, handle, addr, buf, size, read_n):
        key = addr.value if hasattr(addr, "value") else addr
        data = self._reads.get(key, b"")
        for i, byte in enumerate(data):
            buf[i] = byte
        read_n._obj.value = len(data)
        return 1 if data else 0

    def _close_handle(self, handle):
        self.closed.append(handle)
        return 1


def _regions(*rows):
    return list(rows)


def test_reader_requests_only_read_permissions():
    fake_dll = _FakeKernel32(regions=[])
    reader = ks.Win32ProcessReader(4242, kernel32=fake_dll)
    list(reader.regions())
    reader.close()
    # 断言字面值而非常量：两边都用同一个常量时，把常量改错也不会失败（假保证）。
    assert fake_dll.open_calls == [(0x0010 | 0x0400, False, 4242)]
    assert len(fake_dll.closed) == 1              # 句柄被关闭恰好一次


def test_open_process_called_exactly_once_with_read_only_rights():
    """修正案 A §6.4：不能靠 grep 保证安全，运行时断言调用次数与参数。

    删掉句柄缓存（每次都重新 OpenProcess）或改成更高权限后这条失败。
    """
    blob = wrap(K16)
    fake_dll = _FakeKernel32(
        regions=[(0x1000, len(blob), ks.MEM_COMMIT, 0x04)],
        reads={0x1000: blob})
    reader = ks.Win32ProcessReader(4242, kernel32=fake_dll)
    assert reader.read(0x1000, len(blob)) == blob
    assert list(reader.regions()) == [(0x1000, len(blob))]
    reader.close()
    assert fake_dll.open_calls == [(0x0010 | 0x0400, False, 4242)]


def test_close_is_idempotent():
    fake_dll = _FakeKernel32(regions=[])
    reader = ks.Win32ProcessReader(4242, kernel32=fake_dll)
    list(reader.regions())
    reader.close()
    reader.close()
    assert len(fake_dll.closed) == 1


def test_reader_raises_access_denied_on_winerror_5():
    fake_dll = _FakeKernel32(open_result=0, last_error=5)
    reader = ks.Win32ProcessReader(4242, kernel32=fake_dll)
    with pytest.raises(ks.ProcessAccessDenied) as ei:
        list(reader.regions())
    assert ei.value.code == "process_access_denied"
    assert "4242" not in str(ei.value)            # 不泄露 pid


def test_reader_raises_access_denied_on_winerror_1314():
    fake_dll = _FakeKernel32(open_result=0, last_error=1314)
    with pytest.raises(ks.ProcessAccessDenied):
        list(ks.Win32ProcessReader(1, kernel32=fake_dll).regions())


def test_reader_raises_oserror_on_other_open_errors():
    """非权限类失败（例如进程已退出，error 87）不能冒充权限拒绝。"""
    fake_dll = _FakeKernel32(open_result=0, last_error=87)
    with pytest.raises(OSError) as ei:
        list(ks.Win32ProcessReader(1, kernel32=fake_dll).regions())
    assert not isinstance(ei.value, ks.ProcessAccessDenied)


def test_reader_yields_only_committed_readable_regions():
    regions = [
        (0x1000, 4096, ks.MEM_COMMIT, 0x04),       # 合格
        (0x2000, 4096, 0x2000, 0x04),              # State 非 MEM_COMMIT
        (0x3000, 4096, ks.MEM_COMMIT, 0x01),       # PAGE_NOACCESS
        (0x4000, 8192, ks.MEM_COMMIT, 0x40),       # 合格
        (0x5000, 0, ks.MEM_COMMIT, 0x04),          # 零大小，由 scan 层过滤
    ]
    fake_dll = _FakeKernel32(regions=regions)
    got = list(ks.Win32ProcessReader(1, kernel32=fake_dll).regions())
    assert (0x1000, 4096) in got and (0x4000, 8192) in got
    assert (0x2000, 4096) not in got and (0x3000, 4096) not in got


def test_reader_returns_only_bytes_actually_read():
    """部分读取（read_n < 请求长度）时只用实际读到的字节，不返回零填充尾巴。"""
    blob = wrap(K16)
    fake_dll = _FakeKernel32(
        regions=[(0x1000, 4096, ks.MEM_COMMIT, 0x04)],
        reads={0x1000: blob})
    reader = ks.Win32ProcessReader(1, kernel32=fake_dll)
    assert reader.read(0x1000, 4096) == blob
    reader.close()


def test_region_invalidated_between_query_and_read_is_skipped():
    """VirtualQueryEx 之后区域失效（ReadProcessMemory 失败）→ 跳过继续。

    删掉「read 返回 None 就 continue」后，这条会因 exception 失败。
    """
    blob = wrap(K16)
    fake_dll = _FakeKernel32(
        regions=[(0x1000, 4096, ks.MEM_COMMIT, 0x04),
                 (0x2000, len(blob), ks.MEM_COMMIT, 0x04)],
        reads={0x2000: blob},
        last_error=299)                      # ERROR_PARTIAL_COPY，非权限拒绝
    reader = ks.Win32ProcessReader(1, kernel32=fake_dll)
    assert ks.scan_with_reader(reader, lambda c: c == K16,
                               timeout_s=None) == K16


def test_all_reads_failing_returns_none_without_crashing():
    """整个进程都读不到（已退出）时返回 None，不抛异常。"""
    fake_dll = _FakeKernel32(
        regions=[(0x1000, 4096, ks.MEM_COMMIT, 0x04)],
        reads={}, last_error=299)
    reader = ks.Win32ProcessReader(1, kernel32=fake_dll)
    assert ks.scan_with_reader(reader, lambda c: True, timeout_s=None) is None


# ---------- find_qq_pids / scan_key ----------

def test_find_qq_pids_returns_empty_on_non_windows(monkeypatch):
    """非 Windows 必须早退，且不得尝试打开 Win32 DLL。"""
    monkeypatch.setattr(ks.os, "name", "posix")

    def must_not_open(*args, **kwargs):
        pytest.fail("非 Windows 不得打开 Win32 DLL")

    monkeypatch.setattr(ks.ctypes, "WinDLL", must_not_open)
    assert ks.find_qq_pids() == []


def test_scan_key_returns_none_when_no_qq_process(monkeypatch):
    monkeypatch.setattr(ks, "find_qq_pids", lambda: [])
    assert ks.scan_key(lambda c: True) is None


def test_scan_key_returns_key_from_any_qq_process(monkeypatch):
    """R15：多 QQ 进程时，只要有一个进程含该账号密钥就返回它。

    实现若只扫第一个进程、或把「第一个进程没命中」当失败，这条失败。
    """
    blobs = {11: wrap(b"#8NotTheKeyXXXXX"), 22: wrap(K16)}
    monkeypatch.setattr(ks, "find_qq_pids", lambda: [11, 22])

    def reader_for(pid):
        blob = blobs[pid]
        return FakeReader([(0x1000, len(blob))], {0x1000: blob})

    monkeypatch.setattr(ks, "Win32ProcessReader", reader_for)
    assert ks.scan_key(lambda c: c == K16) == K16


def test_scan_key_defers_judgment_entirely_to_verify(monkeypatch):
    """keyscan 不得自作判断：判定只由 verify（选定账号的 page-1 HMAC）决定。"""
    blob = wrap(K16)
    monkeypatch.setattr(ks, "find_qq_pids", lambda: [11])
    monkeypatch.setattr(ks, "Win32ProcessReader",
                        lambda pid: FakeReader([(0x1000, len(blob))],
                                               {0x1000: blob}))
    assert ks.scan_key(lambda c: c == K32) is None


def test_scan_key_skips_process_that_disappeared(monkeypatch):
    """进程枚举与打开之间退出（OSError）→ 跳过该进程继续。"""
    blob = wrap(K16)
    monkeypatch.setattr(ks, "find_qq_pids", lambda: [11, 22])

    def reader_for(pid):
        if pid == 11:
            raise OSError("进程已退出")
        return FakeReader([(0x1000, len(blob))], {0x1000: blob})

    monkeypatch.setattr(ks, "Win32ProcessReader", reader_for)
    assert ks.scan_key(lambda c: c == K16) == K16


def test_scan_key_raises_only_when_everything_denied(monkeypatch):
    """所有进程都权限被拒 → ProcessAccessDenied（doctor 要给管理员提示）。"""
    monkeypatch.setattr(ks, "find_qq_pids", lambda: [11, 22])

    def denied(pid):
        raise ks.ProcessAccessDenied()

    monkeypatch.setattr(ks, "Win32ProcessReader", denied)
    with pytest.raises(ks.ProcessAccessDenied):
        ks.scan_key(lambda c: True)


def test_scan_key_uses_other_process_when_one_is_denied(monkeypatch):
    """一个进程拒绝访问不能拖垮整个扫描：R27 的跳过语义。"""
    blob = wrap(K16)
    monkeypatch.setattr(ks, "find_qq_pids", lambda: [11, 22])

    def reader_for(pid):
        if pid == 11:
            raise ks.ProcessAccessDenied()
        return FakeReader([(0x1000, len(blob))], {0x1000: blob})

    monkeypatch.setattr(ks, "Win32ProcessReader", reader_for)
    assert ks.scan_key(lambda c: c == K16) == K16


def test_scan_key_stops_between_processes_after_deadline(monkeypatch):
    """超时覆盖整个扫描过程：deadline 已过就不再开新进程。"""
    class _FakeTime:
        """只替换 keyscan 模块里的 time，避免影响 pytest 自己的计时。"""

        def __init__(self, values):
            self._values = list(values)
            self._i = 0

        def monotonic(self):
            value = self._values[min(self._i, len(self._values) - 1)]
            self._i += 1
            return value

    opened = []
    blob = wrap(K16)
    monkeypatch.setattr(ks, "find_qq_pids", lambda: [11, 22])
    monkeypatch.setattr(ks, "time", _FakeTime([0.0, 100.0, 200.0]))

    def reader_for(pid):
        opened.append(pid)
        return FakeReader([(0x1000, len(blob))], {0x1000: blob})

    monkeypatch.setattr(ks, "Win32ProcessReader", reader_for)
    assert ks.scan_key(lambda c: False, timeout_s=120.0) is None
    assert opened == [11]


def test_scan_stops_before_verify_when_deadline_passes():
    """超时必须在候选验证循环内生效：一次 verify 也可能很贵（PBKDF2）。

    删掉循环内的 deadline 检查后，calls 会变成 [K16]。
    """
    ticks = iter([0.0, 0.0, 500.0])
    blob = wrap(K16)
    reader = FakeReader([(0x1000, len(blob))], {0x1000: blob})
    calls = []
    got = ks.scan_with_reader(reader, lambda c: calls.append(c) or False,
                              timeout_s=120.0, clock=lambda: next(ticks))
    assert got is None
    assert calls == []


def test_scan_key_dedupes_candidates_across_processes(monkeypatch):
    """同一候选在多个 QQ 进程里都出现时，全局只验证一次。

    删掉 scan_key 里跨进程共享的 seen 后，calls 会变成 [K16, K16]，
    而本机 8 个进程合计把 ~2.3 万个候选放大成 ~10 万次 PBKDF2，导致超时。
    """
    calls = []
    blob = wrap(K16)
    monkeypatch.setattr(ks, "find_qq_pids", lambda: [11, 22])
    monkeypatch.setattr(
        ks, "Win32ProcessReader",
        lambda pid: FakeReader([(0x1000, len(blob))], {0x1000: blob}))

    def verify(cand):
        calls.append(cand)
        return False

    assert ks.scan_key(verify) is None
    assert calls == [K16]


def test_scan_key_closes_reader_for_each_process(monkeypatch):
    """每个进程的句柄都要关：未命中时也不能积累句柄。"""
    blob = wrap(K16)
    readers = []

    def reader_for(pid):
        r = FakeReader([(0x1000, len(blob))], {0x1000: blob})
        readers.append(r)
        return r

    monkeypatch.setattr(ks, "find_qq_pids", lambda: [11, 22])
    monkeypatch.setattr(ks, "Win32ProcessReader", reader_for)
    assert ks.scan_key(lambda c: False) is None
    assert [r.closed for r in readers] == [True, True]


# ---------- 安全边界（grep 只是其中一层，见修正案 A §6.4） ----------

def test_source_contains_no_write_or_inject_apis():
    """spec §12 R7 的机器可执行保障。"""
    src = Path(inspect.getfile(ks)).read_text(encoding="utf-8")
    for forbidden in ("CreateRemoteThread", "WriteProcessMemory",
                      "DebugActiveProcess", "VirtualAllocEx",
                      "SetWindowsHookEx", "VirtualProtectEx",
                      "PROCESS_ALL_ACCESS", "NtWriteVirtualMemory"):
        assert forbidden not in src, f"{forbidden} 不得出现在 keyscan.py 中"


def test_source_never_requests_write_access_flag():
    src = Path(inspect.getfile(ks)).read_text(encoding="utf-8")
    assert "0x0020" not in src          # PROCESS_VM_WRITE
    assert "0x0008" not in src          # PROCESS_VM_OPERATION
