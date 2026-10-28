"""QQ NT 数据库的 SQLCipher 4 页级解密。

算法与常量参照 chatlog-keeper（MIT，见 NOTICE），独立实现。
参照 spec §6.4。禁止凭记忆改动任何常量。

页布局（reserve = 48，QQ NT 9.9.x 实测组合）：
    密文      page[0            : PAGE_SZ - reserve]     4048 B
    IV        page[PAGE_SZ-48   : PAGE_SZ - 48 + 16]     16 B
    HMAC 标签 page[PAGE_SZ-32   : PAGE_SZ - 32 + 20]     20 B（SHA1）
    填充      剩余 12 B

第 1 页特殊：磁盘前 16 字节是明文 salt（不参与解密），
解密后要用 SQLITE_MAGIC 顶替这 16 字节。

`decrypt_page` 输出页尾部的 reserve 区**恒为零填充**，不照抄密文的
IV+HMAC 残余（修正案 A §3.3）：SQLite 按头第 20 字节的声明完全跳过
保留区，填零与照抄对它等价；填零还让明文库里不残留密文片段。
"""
from __future__ import annotations

import hashlib
import hmac
import struct
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

WRAPPER_SZ = 1024
PAGE_SZ = 4096
SALT_SZ = 16
IV_SZ = 16
KEY_SZ = 32
KDF_ITER = 4000          # SQLCipher 默认 256000，QQ 改成了 4000
FAST_ITER = 2

SQLITE_MAGIC = b"SQLite format 3\x00"

# 1024 字节头把 SQLite 的 pending byte 从 0x40000000 推移到 0x3FFFFC00，
# 对应页号 262144（而非 stock SQLite 的 262145）。该页可能全零且不参与认证。
_SQLITE_PENDING_BYTE = 0x40000000
_STRIPPED_PENDING_BYTE = _SQLITE_PENDING_BYTE - WRAPPER_SZ   # 0x3FFFFC00
LOCK_PAGE_NO = _STRIPPED_PENDING_BYTE // PAGE_SZ + 1         # 262144

# (KDF, 页HMAC, reserve) —— 顺序即探测优先级，不得调整。首位为本机预期命中项。
VERIFY_COMBOS: tuple[tuple[str, str, int], ...] = (
    ("sha512", "sha1", 48),
    ("sha512", "sha1", 64),
    ("sha512", "sha1", 80),
    ("sha1", "sha1", 48),
    ("sha512", "sha512", 64),
    ("sha512", "sha512", 80),
    ("sha1", "sha1", 64),
    ("sha1", "sha1", 80),
)

_HASHES = {"sha1": hashlib.sha1, "sha512": hashlib.sha512}
_DIGEST_LEN = {"sha1": 20, "sha512": 64}

assert _STRIPPED_PENDING_BYTE == 0x3FFFFC00
assert LOCK_PAGE_NO == 262144


@dataclass(frozen=True)
class CipherParams:
    kdf: str
    mac: str
    reserve: int
    aes_key: bytes
    mac_key: bytes

    @property
    def fingerprint(self) -> str:
        return f"{self.kdf}/{self.mac}/{self.reserve}"

    def redacted(self) -> dict:
        """可安全写入报告的形式：绝不含密钥材料。"""
        return {"kdf": self.kdf, "mac": self.mac, "reserve": self.reserve,
                "kdf_iter": KDF_ITER, "page_size": PAGE_SZ}


def hash_for(mac: str):
    """返回 mac 名对应的 hashlib 构造器（供本模块与测试 helper 共用）。"""
    return _HASHES[mac]


def digest_len(mac: str) -> int:
    return _DIGEST_LEN[mac]


def combo_is_possible(kdf: str, mac: str, reserve: int) -> bool:
    """该 (KDF, 页HMAC, reserve) 组合在几何上是否可能。

    reserve 至少要容得下 IV 加一个完整摘要。例如 ("sha512","sha512",64)
    就不可能：64 < 16 + 64 = 80。spec §6.4 的表保留了全部 8 项，
    这里用谓词过滤而非删表项。
    """
    return reserve >= IV_SZ + digest_len(mac)


def derive_keys(passphrase: bytes, salt: bytes, kdf: str) -> tuple[bytes, bytes]:
    """返回 (aes_key, mac_key)。mac_salt = salt 每字节异或 0x3A。"""
    aes_key = hashlib.pbkdf2_hmac(kdf, passphrase, salt, KDF_ITER, dklen=KEY_SZ)
    mac_salt = bytes(b ^ 0x3A for b in salt)
    mac_key = hashlib.pbkdf2_hmac(kdf, aes_key, mac_salt, FAST_ITER, dklen=KEY_SZ)
    return aes_key, mac_key


def has_wrapper_header(size_bytes: int) -> bool:
    """QQ NT 库大小必然满足 size % 4096 == 1024。本机实测 335545344 即如此。"""
    return size_bytes % PAGE_SZ == WRAPPER_SZ


def page_count_from_size(size_bytes: int) -> int:
    return (size_bytes - WRAPPER_SZ) // PAGE_SZ


def read_header_and_page1(db_path: Path) -> bytes:
    """只读前 1024+4096 字节，用于参数探测与口令校验。"""
    with open(db_path, "rb") as f:
        return f.read(WRAPPER_SZ + PAGE_SZ)


def decrypt_page(params: CipherParams, page: bytes, page_no: int) -> bytes | None:
    """解密一个 4096 字节的密网页（page_no 从 1 开始）。

    HMAC 校验失败返回 None —— 调用方计入 corrupt_pages 并跳过，不得抛异常。
    全零的 LOCK_PAGE_NO 页原样透传（不参与认证）。
    输出页尾部 reserve 区恒为零填充（见模块 docstring）。
    """
    if len(page) != PAGE_SZ:
        raise ValueError(f"page must be {PAGE_SZ} bytes, got {len(page)}")
    if page_no == LOCK_PAGE_NO and page == b"\x00" * PAGE_SZ:
        return page

    r = params.reserve
    dlen = digest_len(params.mac)
    tail_start = PAGE_SZ - r
    iv = page[tail_start:tail_start + IV_SZ]
    tag = page[tail_start + IV_SZ:tail_start + IV_SZ + dlen]

    if page_no == 1:
        body = page[SALT_SZ:tail_start]
    else:
        body = page[:tail_start]

    expected = hmac.new(params.mac_key,
                        body + iv + struct.pack("<I", page_no),
                        hash_for(params.mac)).digest()
    if not hmac.compare_digest(expected[:dlen], tag):
        return None

    dec = Cipher(algorithms.AES(params.aes_key), modes.CBC(iv)).decryptor()
    plain_body = dec.update(body) + dec.finalize()

    # reserve 区填零，不照抄密文的 IV+HMAC 残余。SQLite 按头第 20 字节的声明
    # 完全跳过保留区，填零与照抄对它等价；填零还让明文库里不残留密文片段。
    zeros = b"\x00" * r
    if page_no == 1:
        return SQLITE_MAGIC + plain_body + zeros
    return plain_body + zeros


def normalize_page1_header(plain_page: bytes, reserve: int) -> bytes:
    """把明文页 1 的第 20 字节（每页保留字节数）显式写成 reserve。

    SQLCipher 会让 SQLite 只用每页前 4096-reserve 字节，并在头里记录该值。
    我们解密后页尾 reserve 区是零填充的（见模块 docstring），SQLite 只按
    头里的声明值跳过保留区；若该声明与实际 reserve 不符，stdlib sqlite3
    会把那段字节当成有效内容而读出损坏数据。显式写入是幂等的防御措施。
    """
    if len(plain_page) != PAGE_SZ:
        raise ValueError(f"page must be {PAGE_SZ} bytes, got {len(plain_page)}")
    if plain_page[:SALT_SZ] != SQLITE_MAGIC:
        raise ValueError("not a plaintext SQLite page 1")
    return plain_page[:20] + bytes([reserve]) + plain_page[21:]


def detect_params(passphrase: bytes, header_and_page1: bytes) -> CipherParams | None:
    """按 VERIFY_COMBOS 顺序试算 page-1 HMAC，首个通过者胜出。

    header_and_page1 = 原始库文件的前 1024+4096 字节（含 QQ 自定义头）。
    也接受裸的 4096 字节页 1（便于测试）。全不通过返回 None。

    派生密钥只依赖 (passphrase, salt, kdf)，因此在一次调用内按 kdf 缓存：
    对错误候选，PBKDF2 从最多 8 次降到 2 次。keyscan 每次拿到候选都会调用
    本函数，这个常数在 ~2 万个候选上是决定性的（本机实测扫描从超时变为可完成）。
    """
    if len(header_and_page1) == PAGE_SZ:
        page1 = header_and_page1
    elif len(header_and_page1) >= WRAPPER_SZ + PAGE_SZ:
        page1 = header_and_page1[WRAPPER_SZ:WRAPPER_SZ + PAGE_SZ]
    else:
        return None

    salt = page1[:SALT_SZ]
    if salt == b"\x00" * SALT_SZ:
        return None

    keys_by_kdf: dict[str, tuple[bytes, bytes]] = {}
    for kdf, mac, reserve in VERIFY_COMBOS:
        if not combo_is_possible(kdf, mac, reserve):
            continue
        if kdf not in keys_by_kdf:
            keys_by_kdf[kdf] = derive_keys(passphrase, salt, kdf)
        aes_key, mac_key = keys_by_kdf[kdf]
        params = CipherParams(kdf, mac, reserve, aes_key, mac_key)
        plain = decrypt_page(params, page1, 1)
        if plain is not None and plain.startswith(SQLITE_MAGIC):
            return params
    return None
