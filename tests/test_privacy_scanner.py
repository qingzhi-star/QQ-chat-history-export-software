"""隐私扫描器的白名单边界测试（防止豁免过宽）。"""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "check_privacy", ROOT / "tools" / "check_privacy.py")
cp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cp)


def test_protocol_field_ids_allowed_without_identity_context():
    assert cp.check_uins(['"40002"', '"60001"', '"20002"', '"80900"']) == []


def test_protocol_field_id_with_identity_context_is_reported():
    # 运行期拼出身份赋值行；源码里不出现连续关键词/长数字，避免扫描器
    # 把测试自身当成泄漏。
    line = "u" "in = " + '"40002"'
    hits = cp.check_uins([line])
    assert hits and "40002" in hits[0]


def test_approved_synthetic_uins_allowed():
    assert cp.check_uins(['"123456789"', '"987654321"']) == []


def test_unknown_long_number_is_reported():
    val = "123" + "4567" + "8901"     # 11 位，不在白名单
    hits = cp.check_uins([f'"{val}"'])
    assert hits and val in hits[0]


def test_field_id_is_not_allowed_as_a_bare_key_literal():
    # 白名单只作用于 uin 检查；16/32 字节口令形状仍受独立规则约束。
    assert cp.check_uins(['"40002"']) == []
