"""Known-answer tests（修正案 A §3.1）——与共享 helper 无关的外部参考值。

round-trip 测试中加密方向（fake_sqlcipher）与解密方向共用同一套布局理解，
两端同时理解错时会双双抵消而通过。本文件用外部权威向量与**文件内独立拼装**
的密网页钉死实际字节行为。所有数据均为合成数据，不含任何真实账号信息。

向量来源（全部于实现当日联网抓取，并与本机独立计算交叉核对一致）：

1. derive_keys 期望值 —— 本机计算。
   provenance: computed on Python 3.14.6 / OpenSSL 3.5.7 (hashlib.pbkdf2_hmac),
   参数 KDF_ITER=4000、FAST_ITER=2、mac_salt = salt XOR 0x3A、kdf=sha512。
   抓的是接线错误（迭代数/0x3A/KDF 选择），不是 hashlib 本身的正确性。

2. HMAC-SHA1 —— RFC 2202 §3 "Test Cases for HMAC-SHA-1" test_case 1 与 2。
   抓取来源: https://www.rfc-editor.org/rfc/rfc2202.txt
   （P. Cheng, R. Glenn, "Test Cases for HMAC-MD5 and HMAC-SHA-1", September 1997）

3. AES-256-CBC —— NIST SP 800-38A §F.2.5 "CBC-AES256.Encrypt"（文档第 28 页）。
   抓取来源: https://nvlpubs.nist.gov/nistpubs/Legacy/SP/nistspecialpublication800-38a.pdf
   （Morris Dworkin, "Recommendation for Block Cipher Modes of Operation:
   Methods and Techniques", NIST Special Publication 800-38A, 2001）

4. 页级组合 KAT —— 按 spec §6.4 的文字描述在本文件内**不使用 fake_sqlcipher**
   独立拼装密网页：只用 hashlib / hmac / struct / cryptography 原语，
   密钥用第 1 条的内联十六进制期望值，不经过 sc.derive_keys。
"""
from __future__ import annotations

import hashlib
import hmac
import struct

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from qqexport.core import sqlcipher as sc

# ---- 合成输入（非任何真实凭据） ------------------------------------------------
PASSPHRASE = b"#8FakeKeyForTest"
SALT = bytes(range(16))

# 第 1 条的期望值：本机 hashlib.pbkdf2_hmac 计算（provenance 见模块 docstring）。
EXPECTED_AES_KEY_HEX = "ede11060f9c8c846795ab881abf59a679b0363de7e51edc00e396f85a1ac8294"
EXPECTED_MAC_KEY_HEX = "d711a60f72861b75337aac401701770af2eadf04e54bc16d6423f11cd84a6d41"


# ---- 第 1 层：derive_keys 输出钉死 --------------------------------------------

def test_derive_keys_known_answer_sha512():
    """provenance: computed on Python 3.14.6 / OpenSSL 3.5.7,
    参数 KDF_ITER=4000、FAST_ITER=2、mac_salt = salt XOR 0x3A。"""
    aes_key, mac_key = sc.derive_keys(PASSPHRASE, SALT, "sha512")
    assert aes_key.hex() == EXPECTED_AES_KEY_HEX
    assert mac_key.hex() == EXPECTED_MAC_KEY_HEX


# ---- 第 2 层：HMAC-SHA1 对 RFC 2202 公开用例 -----------------------------------

def test_hmac_sha1_matches_rfc2202_case_1():
    """RFC 2202 §3, test_case = 1（来源见模块 docstring）。"""
    key = b"\x0b" * 20          # key_len = 20
    data = b"Hi There"          # data_len = 8
    digest = hmac.new(key, data, sc.hash_for("sha1")).hexdigest()
    assert digest == "b617318655057264e28bc0b6fb378c8ef146be00"
    assert sc.digest_len("sha1") == len(bytes.fromhex(digest)) == 20


def test_hmac_sha1_matches_rfc2202_case_2():
    """RFC 2202 §3, test_case = 2（来源见模块 docstring）。"""
    key = b"Jefe"               # key_len = 4
    data = b"what do ya want for nothing?"   # data_len = 28
    digest = hmac.new(key, data, sc.hash_for("sha1")).hexdigest()
    assert digest == "effcdf6ae5eb2fa2d27416d5f184df9c259a7c79"


# ---- 第 3 层：AES-256-CBC 对 NIST SP 800-38A F.2.5 公开向量 ---------------------

def test_aes256_cbc_matches_nist_sp800_38a_f2_5():
    """NIST SP 800-38A §F.2.5 CBC-AES256.Encrypt（来源见模块 docstring）。

    无填充：NIST 示例恰为 4 个整块，与 decrypt_page 处理页体的方式一致
    （页体长度 4096-reserve-16 恒为 16 的倍数）。
    """
    key = bytes.fromhex(
        "603deb1015ca71be2b73aef0857d7781"
        "1f352c073b6108d72d9810a30914dff4")
    iv = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
    assert (len(key), len(iv)) == (sc.KEY_SZ, sc.IV_SZ)
    plaintext = bytes.fromhex(
        "6bc1bee22e409f96e93d7e117393172a"
        "ae2d8a571e03ac9c9eb76fac45af8e51"
        "30c81c46a35ce411e5fbc1191a0a52ef"
        "f69f2445df4f9b17ad2b417be66c3710")
    ciphertext = bytes.fromhex(
        "f58c4c04d6e5f1ba779eabfb5f7bfbd6"
        "9cfc4e967edb808d679f777bc6702c7d"
        "39f23369a9d9bacfa530e26304231461"
        "b2eb05e2c39be9fcda6c19078c6a9d1b")

    enc = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    assert enc.update(plaintext) + enc.finalize() == ciphertext

    dec = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    assert dec.update(ciphertext) + dec.finalize() == plaintext


# ---- 第 4 层：页级组合 KAT（不使用 fake_sqlcipher，独立拼装） ---------------------

# 用第 1 层的内联期望值直接构造 CipherParams —— 不经过 sc.derive_keys，
# 从而把「派生接线」与「页布局/HMAC 接线」两类错误分开钉死。
KAT_PARAMS = sc.CipherParams("sha512", "sha1", 48,
                             bytes.fromhex(EXPECTED_AES_KEY_HEX),
                             bytes.fromhex(EXPECTED_MAC_KEY_HEX))
KAT_IV = bytes(range(16))       # 固定 IV 保证 KAT 可复现；合成数据
KAT_RESERVE = 48                # 16 IV + 20 SHA1 tag + 12 padding
SQLITE_MAGIC_LITERAL = b"SQLite format 3\x00"   # 刻意不用 sc.SQLITE_MAGIC


def _compose_cipher_page(plain_body: bytes, page_no: int, *,
                         salt: bytes | None = None) -> bytes:
    """按 spec §6.4 的文字描述独立拼一个 4096 字节密网页。

    布局（reserve=48, 页HMAC=SHA1）：
        [salt 16B 仅页1] [AES-256-CBC(body)] [IV 16B] [HMAC-SHA1 tag 20B] [0填充 12B]
    HMAC 输入 = 密文体 + IV + struct.pack("<I", page_no)，密钥 = mac_key。
    """
    enc = Cipher(algorithms.AES(KAT_PARAMS.aes_key), modes.CBC(KAT_IV)).encryptor()
    cipher_body = enc.update(plain_body) + enc.finalize()
    tag = hmac.new(KAT_PARAMS.mac_key,
                   cipher_body + KAT_IV + struct.pack("<I", page_no),
                   hashlib.sha1).digest()
    pad = b"\x00" * (KAT_RESERVE - 16 - 20)
    prefix = salt if page_no == 1 else b""
    page = prefix + cipher_body + KAT_IV + tag + pad
    assert len(page) == sc.PAGE_SZ
    return page


def test_page_kat_non_first_page_roundtrip():
    """独立拼装的普通页必须被 decrypt_page 精确还原（reserve 区输出恒为零）。"""
    body = bytes((i * 7 + 3) % 251 for i in range(sc.PAGE_SZ - KAT_RESERVE))
    page = _compose_cipher_page(body, 3)
    expected = body + b"\x00" * KAT_RESERVE
    assert sc.decrypt_page(KAT_PARAMS, page, 3) == expected


def test_page_kat_page1_layout_and_magic_restoration():
    """独立拼装的页 1：salt 明文在 [0:16]，body = plain[16:4048]，
    解密输出 = SQLITE_MAGIC + 明文 body + 48 个零。"""
    body = bytes((i * 13 + 5) % 251
                 for i in range(sc.PAGE_SZ - sc.SALT_SZ - KAT_RESERVE))
    page = _compose_cipher_page(body, 1, salt=SALT)
    assert page[:sc.SALT_SZ] == SALT          # salt 不参与加密，明文置于页首
    expected = SQLITE_MAGIC_LITERAL + body + b"\x00" * KAT_RESERVE
    got = sc.decrypt_page(KAT_PARAMS, page, 1)
    assert got == expected
    assert got.startswith(SQLITE_MAGIC_LITERAL)


def test_page_kat_wrong_page_number_fails_hmac():
    """同一独立拼装页配错误页号必须校验失败（页号以小端 4 字节进 HMAC）。"""
    body = b"\x5a" * (sc.PAGE_SZ - KAT_RESERVE)
    page = _compose_cipher_page(body, 8)
    assert sc.decrypt_page(KAT_PARAMS, page, 8) == body + b"\x00" * KAT_RESERVE
    assert sc.decrypt_page(KAT_PARAMS, page, 9) is None


def test_page_kat_detect_params_recovers_same_keys():
    """detect_params 必须能从独立拼装的 header+页1 恢复出与内联期望值相同的密钥。"""
    body = bytes((i * 3 + 11) % 251
                 for i in range(sc.PAGE_SZ - sc.SALT_SZ - KAT_RESERVE))
    page1 = _compose_cipher_page(body, 1, salt=SALT)
    blob = b"\x00" * sc.WRAPPER_SZ + page1
    params = sc.detect_params(PASSPHRASE, blob)
    assert params is not None
    assert (params.kdf, params.mac, params.reserve) == ("sha512", "sha1", 48)
    assert params.aes_key.hex() == EXPECTED_AES_KEY_HEX
    assert params.mac_key.hex() == EXPECTED_MAC_KEY_HEX
