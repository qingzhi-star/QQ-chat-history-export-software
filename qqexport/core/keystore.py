"""口令的 DPAPI 加密持久化。参照 spec §6.3、修正案 A §7。

口令是解开全部聊天记录的钥匙，因此：

- 用 Windows DPAPI（CurrentUser 作用域 + `CRYPTPROTECT_UI_FORBIDDEN` + 固定
  entropy）保护；同机其他用户与离线读盘都解不出。DPAPI 把密文绑定到
  **当前 Windows 用户 + 当前设备**：`keys.json` 被复制到别的机器或别的用户下，
  密文无法解开（见 DISCLAIMER.md）。
- 口令本体永不进入磁盘明文、日志、异常消息、stdout、报告；对外只暴露
  SHA-256 指纹前 8 位。
- `protect` / `unprotect` 可注入，使全部逻辑在非 Windows 上也能被测。

关键不变量（修正案 A §7.1–7.3）：

- **损坏的 `keys.json` 不得当作空存储。** 存在但解析失败 → 抛 `KeyStoreCorrupt`，
  `save` / `clear` 绝不覆盖；否则会静默抹掉其他账号的密钥。
- **坏条目隔离。** 单条 base64 / 字段 / 类型 / 时间格式不合法 → 该条
  `corrupt=True`、`load()` 返回 None，但不影响其他账号。
- 原子写：`tempfile.mkstemp` 生成唯一临时名 → 同目录 `os.replace`，异常路径
  清理临时文件；写入前 `fsync`。`mkstemp` 以 0o600 创建（Windows 上效果有限，
  属廉价纵深防御）。
"""
from __future__ import annotations

import base64
import binascii
import ctypes
import hashlib
import json
import os
import re
import tempfile
import threading
from ctypes import wintypes
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from .. import config

CRYPTPROTECT_UI_FORBIDDEN = 0x01
ENTROPY = b"qqexport/v1"
FINGERPRINT_LEN = 8
STORE_VERSION = 1

_UIN_RE = re.compile(r"^\d{5,12}$")
_FP_RE = re.compile(r"^[0-9a-f]{8}$")


class KeyStoreUnavailable(RuntimeError):
    """DPAPI 不可用或保护失败。消息不含口令。"""


class KeyStoreCorrupt(RuntimeError):
    """`keys.json` 存在但无法解析。消息给恢复指引，不回显文件内容。"""


def fingerprint(passphrase: bytes) -> str:
    """口令的 SHA-256 指纹前 8 位十六进制：唯一允许外露的口令派生信息。"""
    return hashlib.sha256(passphrase).hexdigest()[:FINGERPRINT_LEN]


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_char))]


def _to_blob(data: bytes) -> tuple[_DATA_BLOB, ctypes.Array]:
    """返回 (blob, 底层缓冲区)。调用方必须同时持有两者，否则缓冲区可能被提前 GC。"""
    buf = ctypes.create_string_buffer(data, max(len(data), 1))
    return _DATA_BLOB(len(data),
                      ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def _dpapi(crypt32, in_data: bytes, entropy: bytes | None,
           func_name: str) -> bytes | None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p

    in_blob, in_keep = _to_blob(in_data)
    ent_blob, ent_keep = _to_blob(entropy) if entropy else (None, None)
    out = _DATA_BLOB()

    fn = getattr(crypt32, func_name)
    fn.argtypes = [ctypes.POINTER(_DATA_BLOB), wintypes.LPCWSTR,
                   ctypes.POINTER(_DATA_BLOB), ctypes.c_void_p,
                   ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DATA_BLOB)]
    fn.restype = wintypes.BOOL
    ok = fn(ctypes.byref(in_blob), None,
            ctypes.byref(ent_blob) if ent_blob else None,
            None, None, CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out))
    del in_keep, ent_keep                    # 调用已完成，此时才可释放缓冲区
    if not ok:
        return None
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


def dpapi_protect(data: bytes) -> bytes | None:
    if os.name != "nt":
        return None
    try:
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    except OSError:
        return None
    return _dpapi(crypt32, data, ENTROPY, "CryptProtectData")


def dpapi_unprotect(blob: bytes) -> bytes | None:
    if os.name != "nt":
        return None
    try:
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    except OSError:
        return None
    return _dpapi(crypt32, blob, ENTROPY, "CryptUnprotectData")


@dataclass(frozen=True)
class StoredKey:
    uin: str
    fingerprint: str
    blob: str            # base64 编码的 DPAPI 密文
    saved_at: str
    corrupt: bool = False


def _utcnow() -> str:
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def _entry_is_valid(uin: str, raw: object) -> bool:
    """单条目的结构校验。任何一项不满足都只影响这一条（修正案 A §7.2）。"""
    if not isinstance(raw, dict):
        return False
    if not isinstance(uin, str) or not _UIN_RE.match(uin):
        return False
    fp = raw.get("fingerprint")
    blob = raw.get("blob")
    saved_at = raw.get("saved_at")
    if not isinstance(fp, str) or not _FP_RE.match(fp):
        return False
    if not isinstance(blob, str) or not blob:
        return False
    if not isinstance(saved_at, str):
        return False
    try:
        base64.b64decode(blob, validate=True)
    except (binascii.Error, ValueError):
        return False
    try:
        datetime.fromisoformat(saved_at)
    except ValueError:
        return False
    return True


class KeyStore:
    def __init__(self, path: Path | None = None,
                 protect: Callable[[bytes], bytes | None] | None = None,
                 unprotect: Callable[[bytes], bytes | None] | None = None,
                 clock: Callable[[], str] | None = None) -> None:
        self._path = Path(path) if path is not None else config.keys_path()
        self._protect = protect if protect is not None else dpapi_protect
        self._unprotect = unprotect if unprotect is not None else dpapi_unprotect
        self._clock = clock if clock is not None else _utcnow
        # 单实例内的 read-modify-write 必须串行，否则并发 save 会互相覆盖（§7.3）。
        self._lock = threading.Lock()

    # ---- 磁盘格式 ----

    def _corrupt(self, reason: str) -> KeyStoreCorrupt:
        return KeyStoreCorrupt(
            f"密钥缓存文件{reason}：{self._path}。"
            "请把它重命名为 "
            f"{self._path.name}.bak 后重试；这只会丢失缓存的密钥"
            "（可重新扫描或手动录入），不会丢失聊天记录。")

    def _read_raw(self) -> dict:
        """读磁盘。文件不存在 → {}；存在但不可解析 → 抛 KeyStoreCorrupt。"""
        if not self._path.is_file():
            return {}
        try:
            text = self._path.read_text(encoding="utf-8")
        except OSError:
            raise self._corrupt("无法读取") from None
        try:
            doc = json.loads(text)
        except ValueError:
            raise self._corrupt("无法解析（JSON 格式错误）") from None
        if not isinstance(doc, dict):
            raise self._corrupt("格式不受支持（顶层不是对象）")
        if doc.get("version") != STORE_VERSION:
            raise self._corrupt(
                f"版本不受支持（需要 {STORE_VERSION}，实际是 "
                f"{doc.get('version')!r}；可能由更新版本的程序写入）")
        keys = doc.get("keys")
        if not isinstance(keys, dict):
            raise self._corrupt("格式不受支持（缺少 keys 对象）")
        return keys

    def _write_raw(self, keys: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"version": STORE_VERSION, "keys": keys},
                             ensure_ascii=False, indent=2)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(self._path.parent),
            prefix=self._path.name + ".", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_name, self._path)
            tmp_name = ""                     # 已替换成功，无需清理
        finally:
            if tmp_name:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass

    # ---- 公开 API ----

    def save(self, uin: str, passphrase: bytes) -> StoredKey:
        protected = self._protect(passphrase)
        if protected is None:
            raise KeyStoreUnavailable(
                "无法用系统 DPAPI 保护口令（当前平台不支持或调用失败）。"
                "可用 --no-cache-key 跳过缓存，本次导出仍可继续。")
        entry = StoredKey(uin=uin, fingerprint=fingerprint(passphrase),
                          blob=base64.b64encode(protected).decode("ascii"),
                          saved_at=self._clock())
        with self._lock:
            keys = self._read_raw()           # 损坏文件在此抛出，绝不覆盖
            keys[uin] = {"fingerprint": entry.fingerprint, "blob": entry.blob,
                         "saved_at": entry.saved_at}
            self._write_raw(keys)
        return entry

    def load(self, uin: str) -> bytes | None:
        with self._lock:
            keys = self._read_raw()
        raw = keys.get(uin)
        if not _entry_is_valid(uin, raw):
            return None
        try:
            protected = base64.b64decode(raw["blob"], validate=True)
        except (binascii.Error, ValueError):
            return None
        return self._unprotect(protected)

    def clear(self, uin: str | None = None) -> int:
        with self._lock:
            keys = self._read_raw()
            if uin is None:
                n = len(keys)
                if n == 0:
                    return 0
                keys = {}
            elif uin in keys:
                del keys[uin]
                n = 1
            else:
                return 0
            self._write_raw(keys)
        return n

    def entries(self) -> list[StoredKey]:
        with self._lock:
            keys = self._read_raw()
        out: list[StoredKey] = []
        for uin, raw in sorted(keys.items()):
            if _entry_is_valid(uin, raw):
                out.append(StoredKey(
                    uin=uin, fingerprint=raw["fingerprint"], blob=raw["blob"],
                    saved_at=raw["saved_at"]))
            else:
                # 损坏条目的描述不回显 blob 原文（修正案 A §7.5）。
                out.append(StoredKey(uin=uin, fingerprint="", blob="",
                                     saved_at="", corrupt=True))
        return out

    def redacted_dump(self) -> list[dict]:
        """可安全写入报告 / stdout 的形式：不含 blob、不含口令。"""
        return [{"uin": e.uin, "fingerprint": e.fingerprint,
                 "saved_at": e.saved_at} for e in self.entries()]
