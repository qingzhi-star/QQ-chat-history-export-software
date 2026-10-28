"""真库页 1 fixture 的结构断言（修正案 A §11）。

fixture 缺失时整个模块 SKIP——这是**正确状态**，不是失败：它要求操作者
在真机上运行 `tools/capture_fixtures.py`、逐条审查页 1 的可打印字符串并输入
确认短语。不得为了让测试变绿而降低隐私门槛（§11.5）。

口令一律在测试进程内从 `KeyStore` 读取，**不经过环境变量**（§11.1）。

marker：`fixture`（文件缺失则 skip）。
"""
import json
import re
from pathlib import Path

import pytest

from qqexport.core import sqlcipher as sc

FIXTURE = Path(__file__).parent / "fixtures" / "nt_msg_header_page1.bin"
PROVENANCE = Path(__file__).parent / "fixtures" / "provenance.json"

pytestmark = [
    pytest.mark.fixture,
    pytest.mark.skipif(
        not FIXTURE.is_file(),
        reason="缺少 tests/fixtures/nt_msg_header_page1.bin；"
               "在真机运行 tools/capture_fixtures.py 并人工确认后 git add -f"),
]

ALLOWED_PROVENANCE_FIELDS = {
    "captured_at", "qq_version", "source_page_count", "has_wrapper_header",
    "captured_bytes", "cipher_fingerprint",
}


@pytest.fixture(scope="module")
def blob() -> bytes:
    return FIXTURE.read_bytes()


@pytest.fixture(scope="module")
def real_passphrase() -> bytes:
    """从本机 KeyStore 取缓存口令；没有就 skip（不走环境变量）。"""
    from qqexport.core import discover, keystore

    try:
        accounts = discover.discover_accounts()
    except discover.DiscoveryError:
        pytest.skip("本机未发现账号，无法读取缓存口令")
    for account in accounts:
        try:
            key = keystore.KeyStore().load(account.uin)
        except keystore.KeyStoreCorrupt:
            pytest.skip("keys.json 损坏，无法读取缓存口令")
        if key is not None:
            return key
    pytest.skip("没有缓存口令；先运行 `qqexport key scan` 或 `qqexport key set`")


def test_fixture_is_exactly_header_plus_page1(blob):
    assert len(blob) == sc.WRAPPER_SZ + sc.PAGE_SZ == 5120


def test_page1_salt_is_not_all_zero(blob):
    page1 = blob[sc.WRAPPER_SZ:sc.WRAPPER_SZ + sc.PAGE_SZ]
    assert page1[:sc.SALT_SZ] != b"\x00" * sc.SALT_SZ


def test_page1_is_not_plaintext(blob):
    """未解密时绝不应出现 SQLite 魔数——证明它确实是加密的。"""
    page1 = blob[sc.WRAPPER_SZ:sc.WRAPPER_SZ + sc.PAGE_SZ]
    assert not page1.startswith(sc.SQLITE_MAGIC)


def test_wrapper_header_is_not_a_sqlite_file(blob):
    assert not blob.startswith(sc.SQLITE_MAGIC)


def test_fixture_size_shape_is_consistent_with_wrapper(blob):
    """整库大小必然满足 size % 4096 == 1024；fixture 是其前缀，同样成立。"""
    assert sc.has_wrapper_header(len(blob))


def test_provenance_is_minimal_and_has_no_identity():
    if not PROVENANCE.is_file():
        pytest.skip("provenance.json 未采集")
    doc = json.loads(PROVENANCE.read_text(encoding="utf-8"))
    assert set(doc) == ALLOWED_PROVENANCE_FIELDS, (
        f"provenance 字段集不符（多出 {set(doc) - ALLOWED_PROVENANCE_FIELDS}）")
    # 日期粗化到天，不含时分秒
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", doc["captured_at"])
    assert doc["captured_bytes"] == 5120
    assert doc["has_wrapper_header"] is True
    assert isinstance(doc["source_page_count"], int) and doc["source_page_count"] > 0
    assert doc["qq_version"]
    # 任何字段都不应看起来像口令本体
    for key, value in doc.items():
        assert not (isinstance(value, str) and key != "captured_at"
                    and len(value) in (16, 32) and value.isprintable()), \
            f"字段 {key} 看起来像口令本体"


def test_detect_params_succeeds_against_real_page1(blob, real_passphrase):
    """这是「常量与 QQ 实际行为一致」的关键证据。"""
    params = sc.detect_params(real_passphrase, blob)
    assert params is not None, (
        "八组合均未通过真库 page-1 HMAC 校验——常量可能已过期")
    assert params.fingerprint == "sha512/sha1/48"


def test_real_page1_decrypts_to_sqlite_magic(blob, real_passphrase):
    params = sc.detect_params(real_passphrase, blob)
    assert params is not None
    page1 = blob[sc.WRAPPER_SZ:sc.WRAPPER_SZ + sc.PAGE_SZ]
    plain = sc.decrypt_page(params, page1, 1)
    assert plain is not None
    assert plain.startswith(sc.SQLITE_MAGIC)
    assert plain[20] == params.reserve
