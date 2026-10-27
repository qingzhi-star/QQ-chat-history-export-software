"""report 的单元测试：状态、渲染、JSON 与 schema_version。"""
import json

import pytest

from qqexport import report as rp


def c(i, status, name="check", detail=""):
    return rp.CheckResult(index=i, name=name, status=status, detail=detail)


def test_status_glyphs_cover_all_statuses():
    assert set(rp.STATUS_GLYPH) == {"ok", "warn", "fail", "skip"}
    assert set(rp.STATUS_ASCII) == {"ok", "warn", "fail", "skip"}


def test_ok_is_true_only_when_no_fail():
    assert rp.DoctorReport([c(1, "ok"), c(2, "warn")], "t").ok is True
    assert rp.DoctorReport([c(1, "ok"), c(2, "fail")], "t").ok is False
    assert rp.DoctorReport([c(1, "skip")], "t").ok is True


def test_failed_returns_only_fail_entries():
    r = rp.DoctorReport([c(1, "ok"), c(2, "fail", "b"), c(3, "fail", "c")], "t")
    assert [x.name for x in r.failed] == ["b", "c"]


def test_to_text_has_one_line_per_check():
    r = rp.DoctorReport([c(1, "ok", "python"), c(2, "fail", "qq", "boom")], "t")
    lines = [ln for ln in r.to_text().splitlines() if ln.strip()]
    assert any("python" in ln for ln in lines)
    assert any("qq" in ln and "boom" in ln for ln in lines)


def test_to_text_ascii_only_has_no_non_ascii_chars():
    """中文 Windows 控制台常是 cp936，emoji 会直接 UnicodeEncodeError。"""
    r = rp.DoctorReport([c(1, "ok", "python"), c(2, "fail", "qq", "boom")], "t")
    text = r.to_text(ascii_only=True)
    assert text.encode("ascii", "strict")
    assert "[OK]" in text and "[FAIL]" in text


def test_to_text_default_uses_glyphs():
    r = rp.DoctorReport([c(1, "ok", "python")], "t")
    assert rp.STATUS_GLYPH["ok"] in r.to_text()


def test_to_json_is_serialisable():
    r = rp.DoctorReport([c(1, "ok", "python", "3.14.6")], "2026-10-24T00:00:00")
    doc = json.loads(json.dumps(r.to_json(), ensure_ascii=False))
    assert doc["generated_at"] == "2026-10-24T00:00:00"
    assert doc["checks"][0] == {"index": 1, "name": "python",
                                "status": "ok", "detail": "3.14.6"}
    assert doc["ok"] is True


def test_to_json_has_schema_version():
    r = rp.DoctorReport([c(1, "ok", "python")], "t")
    assert r.to_json()["schema_version"] == rp.SCHEMA_VERSION == 1


def test_check_result_rejects_unknown_status():
    with pytest.raises(ValueError, match="status"):
        rp.CheckResult(1, "x", "bogus_status")
