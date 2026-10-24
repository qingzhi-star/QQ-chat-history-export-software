"""测试专用：QQ NT SQLCipher 4 格式的**加密**方向。

用它把随机字节页变成合成密文页，从而在无隐私数据、无需实时 QQ 的前提下
穷举验证 sqlcipher.py 的解密正确性。只存在于 tests/，禁止被 qqexport 包导入。
"""
from __future__ import annotations

import hmac
import os
import struct

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from qqexport.core import sqlcipher as sc


def encrypt_page(plain: bytes, page_no: int, *, aes_key: bytes, mac_key: bytes,
                 mac: str, reserve: int, salt: bytes | None = None) -> bytes:
    """plain 为 4096 字节明网页，返回 4096 字节密网页。

    page_no == 1 时：plain 的 [0:16] 必须是 SQLITE_MAGIC，密文 [0:16] 放 salt。
    """
    if len(plain) != sc.PAGE_SZ:
        raise ValueError("plain page must be 4096 bytes")
    dlen = sc.digest_len(mac)
    if reserve < sc.IV_SZ + dlen:
        raise ValueError(f"reserve {reserve} too small for IV+{dlen}B tag")

    iv = os.urandom(sc.IV_SZ)
    tail_start = sc.PAGE_SZ - reserve
    if page_no == 1:
        if plain[:sc.SALT_SZ] != sc.SQLITE_MAGIC:
            raise ValueError("page 1 plaintext must start with the SQLite magic")
        if salt is None or len(salt) != sc.SALT_SZ:
            raise ValueError("page 1 requires a 16-byte salt")
        body = plain[sc.SALT_SZ:tail_start]
        prefix = salt
    else:
        body = plain[:tail_start]
        prefix = b""

    enc = Cipher(algorithms.AES(aes_key), modes.CBC(iv)).encryptor()
    cipher_body = enc.update(body) + enc.finalize()
    tag = hmac.new(mac_key, cipher_body + iv + struct.pack("<I", page_no),
                   sc.hash_for(mac)).digest()[:dlen]
    pad = b"\x00" * (reserve - sc.IV_SZ - dlen)
    out = prefix + cipher_body + iv + tag + pad
    assert len(out) == sc.PAGE_SZ, len(out)
    return out


def build_encrypted_db(plain_pages: list[bytes], passphrase: bytes, *,
                       kdf: str = "sha512", mac: str = "sha1", reserve: int = 48,
                       salt: bytes | None = None,
                       wrapper: bytes | None = None) -> bytes:
    """把明网页序列打包成完整的 QQ NT 格式库字节（含 1024 字节头）。

    plain_pages[0] 必须以 SQLITE_MAGIC 开头。
    """
    if not plain_pages:
        raise ValueError("need at least one page")
    if plain_pages[0][:sc.SALT_SZ] != sc.SQLITE_MAGIC:
        raise ValueError("page 1 must start with the SQLite magic")
    salt = salt or os.urandom(sc.SALT_SZ)
    aes_key, mac_key = sc.derive_keys(passphrase, salt, kdf)

    out = bytearray(wrapper if wrapper is not None else b"\x00" * sc.WRAPPER_SZ)
    for i, plain in enumerate(plain_pages, start=1):
        out += encrypt_page(plain, i, aes_key=aes_key, mac_key=mac_key,
                            mac=mac, reserve=reserve,
                            salt=salt if i == 1 else None)
    return bytes(out)


def random_plain_pages(n: int, *, seed: int = 20261024, reserve: int = 48) -> list[bytes]:
    """n 个随机明网页；页 1 以 SQLITE_MAGIC 开头，第 20 字节写 reserve。

    每页最后 reserve 字节置零：SQLCipher 的明网页保留区本就无语义，
    而 decrypt_page 输出零填充的保留区，置零后 round-trip 才能字节相等。
    """
    import random
    rng = random.Random(seed)
    pages: list[bytes] = []
    for i in range(n):
        p = bytearray(rng.randbytes(sc.PAGE_SZ))
        p[sc.PAGE_SZ - reserve:] = b"\x00" * reserve
        if i == 0:
            p[0:sc.SALT_SZ] = sc.SQLITE_MAGIC
            p[20] = reserve
        pages.append(bytes(p))
    return pages
