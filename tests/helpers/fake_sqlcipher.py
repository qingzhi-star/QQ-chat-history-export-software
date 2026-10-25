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


WAL_MAGIC_LE = 0x377F0682
WAL_MAGIC_BE = 0x377F0683
WAL_VERSION = 3007000
DEFAULT_WAL_SALT = (0x11111111, 0x22222222)


def wal_checksum(data: bytes, s0: int, s1: int, big: bool) -> tuple[int, int]:
    fmt = ">II" if big else "<II"
    m = 0xFFFFFFFF
    for i in range(0, len(data), 8):
        v0, v1 = struct.unpack_from(fmt, data, i)
        s0 = (s0 + v0 + s1) & m
        s1 = (s1 + v1 + s0) & m
    return s0, s1


def build_wal(entries, *, aes_key: bytes, mac_key: bytes, mac: str = "sha1",
              reserve: int = 48, db_salt: bytes | None = None,
              page_size: int = sc.PAGE_SZ,
              salt: tuple[int, int] = DEFAULT_WAL_SALT,
              big_endian: bool = False, checkpoint_seq: int = 0) -> bytes:
    """构造一个结构完整的 WAL 文件字节。

    entries: [(pgno, plain_page_4096B, dbsize_after_commit), ...]
             dbsize_after_commit == 0 表示非 commit 帧。
    任何 pgno == 1 的条目都要求传入 db_salt（与主库 page-1 salt 相同）。
    """
    magic = WAL_MAGIC_BE if big_endian else WAL_MAGIC_LE
    hdr = struct.pack(">IIII", magic, WAL_VERSION, page_size, checkpoint_seq)
    hdr += struct.pack(">II", *salt)
    s0, s1 = wal_checksum(hdr[:24], 0, 0, big_endian)
    out = bytearray(hdr + struct.pack(">II", s0, s1))

    for pgno, plain, dbsize in entries:
        cipher = encrypt_page(plain, pgno, aes_key=aes_key, mac_key=mac_key,
                              mac=mac, reserve=reserve,
                              salt=db_salt if pgno == 1 else None)
        fh = struct.pack(">II", pgno, dbsize) + struct.pack(">II", *salt)
        s0, s1 = wal_checksum(fh[:8] + cipher, s0, s1, big_endian)
        out += fh + struct.pack(">II", s0, s1) + cipher
    return bytes(out)


def build_shm(*, page_size: int, mx_frame: int, n_page: int,
              salt: tuple[int, int] = DEFAULT_WAL_SALT,
              frame_cksum: tuple[int, int] = (0, 0),
              big_endian: bool = False) -> bytes:
    """构造一份自洽的 -shm（两份相同的 48 字节头）。字段布局取自 SQLite WalIndexHdr。"""
    fmt = ">" if big_endian else "<"
    hdr = bytearray(48)
    struct.pack_into(fmt + "I", hdr, 0, 1)              # iVersion
    struct.pack_into(fmt + "I", hdr, 8, 1)              # iChange
    hdr[13] = 1 if big_endian else 0                    # bBigEndCksum
    struct.pack_into(fmt + "I", hdr, 16, mx_frame)      # mxFrame
    struct.pack_into(fmt + "I", hdr, 20, n_page)        # nPage
    struct.pack_into(fmt + "II", hdr, 24, *frame_cksum) # aFrameCksum
    struct.pack_into(fmt + "II", hdr, 32, *salt)        # aSalt
    struct.pack_into(fmt + "II", hdr, 40,
                     *wal_checksum(bytes(hdr[:40]), 0, 0, big_endian))
    return bytes(hdr) * 2


def tamper_page_and_resign(blob: bytes, frame_index: int, *,
                           big_endian: bool = False, byte_offset: int = 0,
                           xor: int = 0xFF) -> bytes:
    """篡改第 frame_index（1-based）帧的**密文**，并重算自该帧起的所有累计校验和。

    用来构造「SQLite 帧校验和有效、但 SQLCipher 页 HMAC 无效」的帧——
    这是唯一能单独触发页级 HMAC 失败路径的办法，因为直接改密文会连带
    破坏帧校验和（帧校验和覆盖 fh[:8] + 整个密网页）。

    注意：WAL 头与帧头的整数字段恒为大端；big_endian 只影响校验和**运算**
    时对数据字的读取字节序。
    """
    out = bytearray(blob)
    encoded = struct.unpack(">I", blob[8:12])[0]
    page_size = 65536 if encoded == 1 else encoded
    frame_sz = 24 + page_size

    target = 32 + (frame_index - 1) * frame_sz + 24 + byte_offset
    if target >= len(out):
        raise IndexError(f"frame {frame_index} not present in this WAL")
    out[target] ^= xor

    s0, s1 = wal_checksum(bytes(out[:24]), 0, 0, big_endian)
    n_frames = (len(out) - 32) // frame_sz
    for i in range(1, n_frames + 1):
        base = 32 + (i - 1) * frame_sz
        chunk = bytes(out[base:base + 8]) + bytes(out[base + 24:base + frame_sz])
        s0, s1 = wal_checksum(chunk, s0, s1, big_endian)
        struct.pack_into(">II", out, base + 16, s0, s1)
    return bytes(out)
