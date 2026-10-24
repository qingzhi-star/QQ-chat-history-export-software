import hashlib

import pytest

from qqexport.core import sqlcipher as sc
from tests.helpers import fake_sqlcipher as fake

PASSPHRASE = b"#8FakeKeyForTest"          # 恰好 16 字节可打印 ASCII
ALL_COMBOS = sc.VERIFY_COMBOS
# ("sha512","sha512",64) 几何上不可能（64 < 16 + 64），遍历时必须排除
POSSIBLE_COMBOS = tuple(c for c in ALL_COMBOS if sc.combo_is_possible(*c))


def test_constants_match_spec_section_6_4():
    assert (sc.WRAPPER_SZ, sc.PAGE_SZ, sc.SALT_SZ, sc.IV_SZ, sc.KEY_SZ,
            sc.KDF_ITER, sc.FAST_ITER) == (1024, 4096, 16, 16, 32, 4000, 2)
    assert sc.SQLITE_MAGIC == b"SQLite format 3\x00"


def test_lock_page_derivation():
    assert 0x40000000 - sc.WRAPPER_SZ == 0x3FFFFC00
    assert sc.LOCK_PAGE_NO == 262144


def test_verify_combos_are_eight_and_ordered():
    assert len(sc.VERIFY_COMBOS) == 8
    assert sc.VERIFY_COMBOS[0] == ("sha512", "sha1", 48)


def test_wrapper_header_matches_real_measured_db_size():
    """spec §3 实测：nt_msg.db = 335545344 = 81920*4096 + 1024。"""
    assert sc.has_wrapper_header(335_545_344)
    assert sc.page_count_from_size(335_545_344) == 81_920
    assert not sc.has_wrapper_header(335_544_320)


def test_derive_keys_is_deterministic_and_mac_salt_is_xored():
    salt = bytes(range(16))
    k1 = sc.derive_keys(PASSPHRASE, salt, "sha512")
    k2 = sc.derive_keys(PASSPHRASE, salt, "sha512")
    assert k1 == k2
    assert len(k1[0]) == len(k1[1]) == 32
    expected_mac_salt = bytes(b ^ 0x3A for b in salt)
    assert expected_mac_salt != salt
    assert k1[1] == hashlib.pbkdf2_hmac("sha512", k1[0], expected_mac_salt, 2, 32)


def test_hash_for_maps_mac_names_to_hashlib_constructors():
    assert sc.hash_for("sha1") is hashlib.sha1
    assert sc.hash_for("sha512") is hashlib.sha512
    assert sc.digest_len("sha1") == 20
    assert sc.digest_len("sha512") == 64


@pytest.mark.parametrize("kdf,mac,reserve", POSSIBLE_COMBOS)
def test_roundtrip_non_first_page(kdf, mac, reserve):
    salt = bytes(range(16))
    aes_key, mac_key = sc.derive_keys(PASSPHRASE, salt, kdf)
    params = sc.CipherParams(kdf, mac, reserve, aes_key, mac_key)
    # 保留区置零：SQLCipher 明网页的保留区无语义，decrypt_page 输出零填充
    plain = (bytes(range(256)) * 16)[:sc.PAGE_SZ - reserve] + b"\x00" * reserve
    assert len(plain) == sc.PAGE_SZ
    cipher = fake.encrypt_page(plain, 7, aes_key=aes_key, mac_key=mac_key,
                               mac=mac, reserve=reserve)
    assert len(cipher) == sc.PAGE_SZ
    assert sc.decrypt_page(params, cipher, 7) == plain


@pytest.mark.parametrize("kdf,mac,reserve", POSSIBLE_COMBOS)
def test_roundtrip_page1_restores_magic(kdf, mac, reserve):
    salt = bytes(range(16))
    aes_key, mac_key = sc.derive_keys(PASSPHRASE, salt, kdf)
    params = sc.CipherParams(kdf, mac, reserve, aes_key, mac_key)
    plain = (sc.SQLITE_MAGIC + b"\xab" * (sc.PAGE_SZ - sc.SALT_SZ - reserve)
             + b"\x00" * reserve)
    assert len(plain) == sc.PAGE_SZ
    cipher = fake.encrypt_page(plain, 1, aes_key=aes_key, mac_key=mac_key,
                               mac=mac, reserve=reserve, salt=salt)
    assert cipher[:16] == salt              # salt 明文置于页首
    got = sc.decrypt_page(params, cipher, 1)
    assert got == plain
    assert got.startswith(sc.SQLITE_MAGIC)


def test_decrypt_page_zeroes_the_reserve_region():
    """明文库中不应残留密文的 IV/HMAC 片段。"""
    salt = bytes(range(16))
    aes_key, mac_key = sc.derive_keys(PASSPHRASE, salt, "sha512")
    params = sc.CipherParams("sha512", "sha1", 48, aes_key, mac_key)
    plain = (b"\x11" * (sc.PAGE_SZ - 48)) + b"\x00" * 48
    cipher = fake.encrypt_page(plain, 9, aes_key=aes_key, mac_key=mac_key,
                               mac="sha1", reserve=48)
    assert cipher[sc.PAGE_SZ - 48:] != b"\x00" * 48        # 密文保留区非零
    assert sc.decrypt_page(params, cipher, 9)[sc.PAGE_SZ - 48:] == b"\x00" * 48


def test_combo_is_possible_rejects_geometrically_impossible_combos():
    assert sc.combo_is_possible("sha512", "sha1", 48) is True     # 48 >= 16+20
    assert sc.combo_is_possible("sha512", "sha512", 80) is True   # 80 >= 16+64
    assert sc.combo_is_possible("sha512", "sha512", 64) is False  # 64 <  16+64
    impossible = [c for c in sc.VERIFY_COMBOS if not sc.combo_is_possible(*c)]
    assert impossible == [("sha512", "sha512", 64)]
    assert len(POSSIBLE_COMBOS) == len(sc.VERIFY_COMBOS) - 1 == 7


def test_page_number_is_mixed_into_hmac():
    """同一密文配错误页号必须校验失败——证明 pack('<I', page_no) 参与了 HMAC。"""
    salt = bytes(range(16))
    aes_key, mac_key = sc.derive_keys(PASSPHRASE, salt, "sha512")
    params = sc.CipherParams("sha512", "sha1", 48, aes_key, mac_key)
    # 保留区置零：decrypt_page 的输出页尾部 reserve 区恒为零（修正案 §3.3）
    plain = b"\x11" * (sc.PAGE_SZ - 48) + b"\x00" * 48
    cipher = fake.encrypt_page(plain, 5, aes_key=aes_key, mac_key=mac_key,
                               mac="sha1", reserve=48)
    assert sc.decrypt_page(params, cipher, 5) == plain
    assert sc.decrypt_page(params, cipher, 6) is None


def test_wrong_passphrase_is_rejected():
    salt = bytes(range(16))
    aes_key, mac_key = sc.derive_keys(PASSPHRASE, salt, "sha512")
    cipher = fake.encrypt_page(b"\x11" * sc.PAGE_SZ, 3, aes_key=aes_key,
                               mac_key=mac_key, mac="sha1", reserve=48)
    bad_aes, bad_mac = sc.derive_keys(b"#8WrongKeyXXXXXX", salt, "sha512")
    bad = sc.CipherParams("sha512", "sha1", 48, bad_aes, bad_mac)
    assert sc.decrypt_page(bad, cipher, 3) is None


def test_single_bit_flip_in_ciphertext_is_rejected():
    salt = bytes(range(16))
    aes_key, mac_key = sc.derive_keys(PASSPHRASE, salt, "sha512")
    params = sc.CipherParams("sha512", "sha1", 48, aes_key, mac_key)
    cipher = bytearray(fake.encrypt_page(b"\x11" * sc.PAGE_SZ, 3,
                                         aes_key=aes_key, mac_key=mac_key,
                                         mac="sha1", reserve=48))
    cipher[100] ^= 0x01
    assert sc.decrypt_page(params, bytes(cipher), 3) is None


def test_multipage_roundtrip_is_byte_exact():
    plain_pages = fake.random_plain_pages(9)
    blob = fake.build_encrypted_db(plain_pages, PASSPHRASE)
    assert sc.has_wrapper_header(len(blob))
    salt = blob[sc.WRAPPER_SZ:sc.WRAPPER_SZ + sc.SALT_SZ]
    aes_key, mac_key = sc.derive_keys(PASSPHRASE, salt, "sha512")
    params = sc.CipherParams("sha512", "sha1", 48, aes_key, mac_key)
    for i, expected in enumerate(plain_pages, start=1):
        off = sc.WRAPPER_SZ + (i - 1) * sc.PAGE_SZ
        got = sc.decrypt_page(params, blob[off:off + sc.PAGE_SZ], i)
        assert got == expected, f"page {i} mismatch"


def test_detect_params_finds_expected_combo_first():
    plain_pages = fake.random_plain_pages(2)
    blob = fake.build_encrypted_db(plain_pages, PASSPHRASE,
                                   kdf="sha512", mac="sha1", reserve=48)
    params = sc.detect_params(PASSPHRASE, blob[:sc.WRAPPER_SZ + sc.PAGE_SZ])
    assert params is not None
    assert (params.kdf, params.mac, params.reserve) == ("sha512", "sha1", 48)
    assert params.fingerprint == "sha512/sha1/48"


@pytest.mark.parametrize("kdf,mac,reserve", POSSIBLE_COMBOS)
def test_detect_params_works_for_every_combo(kdf, mac, reserve):
    blob = fake.build_encrypted_db(fake.random_plain_pages(1, reserve=reserve),
                                   PASSPHRASE, kdf=kdf, mac=mac, reserve=reserve)
    params = sc.detect_params(PASSPHRASE, blob[:sc.WRAPPER_SZ + sc.PAGE_SZ])
    assert params is not None
    assert (params.kdf, params.mac, params.reserve) == (kdf, mac, reserve)


def test_detect_params_rejects_wrong_passphrase():
    blob = fake.build_encrypted_db(fake.random_plain_pages(1), PASSPHRASE)
    assert sc.detect_params(b"#8WrongKeyXXXXXX",
                            blob[:sc.WRAPPER_SZ + sc.PAGE_SZ]) is None


def test_detect_params_rejects_all_zero_salt():
    blob = fake.build_encrypted_db(fake.random_plain_pages(1), PASSPHRASE,
                                   salt=b"\x00" * 16)
    assert sc.detect_params(PASSPHRASE, blob[:sc.WRAPPER_SZ + sc.PAGE_SZ]) is None


def test_detect_params_accepts_bare_page1():
    blob = fake.build_encrypted_db(fake.random_plain_pages(1), PASSPHRASE)
    page1 = blob[sc.WRAPPER_SZ:sc.WRAPPER_SZ + sc.PAGE_SZ]
    assert sc.detect_params(PASSPHRASE, page1) is not None


def test_detect_params_rejects_short_input():
    assert sc.detect_params(PASSPHRASE, b"\x00" * 100) is None


def test_lock_page_all_zero_passes_through_unauthenticated():
    params = sc.CipherParams("sha512", "sha1", 48, b"\x00" * 32, b"\x00" * 32)
    zeros = b"\x00" * sc.PAGE_SZ
    assert sc.decrypt_page(params, zeros, sc.LOCK_PAGE_NO) == zeros


def test_non_zero_lock_page_still_requires_valid_hmac():
    params = sc.CipherParams("sha512", "sha1", 48, b"\x00" * 32, b"\x00" * 32)
    assert sc.decrypt_page(params, b"\x01" * sc.PAGE_SZ, sc.LOCK_PAGE_NO) is None


def test_decrypt_page_rejects_wrong_length():
    params = sc.CipherParams("sha512", "sha1", 48, b"\x00" * 32, b"\x00" * 32)
    with pytest.raises(ValueError, match="4096"):
        sc.decrypt_page(params, b"\x00" * 100, 2)


def test_normalize_page1_sets_reserved_byte():
    plain = sc.SQLITE_MAGIC + b"\x00" * 4 + b"\xff" + b"\x00" * (sc.PAGE_SZ - 21)
    assert plain[20] == 0xFF
    out = sc.normalize_page1_header(plain, 48)
    assert out[20] == 48
    assert out[:20] == plain[:20] and out[21:] == plain[21:]
    assert len(out) == sc.PAGE_SZ


def test_normalize_page1_is_idempotent():
    plain = sc.SQLITE_MAGIC + b"\x00" * (sc.PAGE_SZ - 16)
    once = sc.normalize_page1_header(plain, 48)
    assert sc.normalize_page1_header(once, 48) == once


def test_normalize_page1_rejects_non_page1():
    with pytest.raises(ValueError, match="SQLite page 1"):
        sc.normalize_page1_header(b"\x00" * sc.PAGE_SZ, 48)


def test_redacted_exposes_no_key_material():
    params = sc.CipherParams("sha512", "sha1", 48, b"A" * 32, b"B" * 32)
    text = repr(params.redacted())
    assert "A" * 32 not in text and "B" * 32 not in text
    assert params.redacted() == {"kdf": "sha512", "mac": "sha1", "reserve": 48,
                                 "kdf_iter": 4000, "page_size": 4096}


def test_read_header_and_page1_reads_exactly_5120_bytes(tmp_path):
    blob = fake.build_encrypted_db(fake.random_plain_pages(4), PASSPHRASE)
    p = tmp_path / "nt_msg.db"
    p.write_bytes(blob)
    got = sc.read_header_and_page1(p)
    assert len(got) == sc.WRAPPER_SZ + sc.PAGE_SZ == 5120
    assert got == blob[:5120]


def test_exception_messages_are_printable_ascii_without_sensitive_content():
    """修正案 §3.4：异常消息不得含口令、原始页二进制或数据库路径。

    只允许可打印 ASCII，且不得出现路径分隔符（Windows/POSIX）与控制字符。
    """
    params = sc.CipherParams("sha512", "sha1", 48, b"\x00" * 32, b"\x00" * 32)
    messages: list[str] = []
    with pytest.raises(ValueError) as e1:
        sc.decrypt_page(params, b"\x00" * 100, 2)
    messages.append(str(e1.value))
    with pytest.raises(ValueError) as e2:
        sc.normalize_page1_header(b"\xff" * sc.PAGE_SZ, 48)
    messages.append(str(e2.value))
    with pytest.raises(ValueError) as e3:
        sc.normalize_page1_header(b"\x00" * 100, 48)
    messages.append(str(e3.value))
    for msg in messages:
        assert msg.isascii() and msg.isprintable()
        assert "\\" not in msg and "/" not in msg and ":" not in msg
