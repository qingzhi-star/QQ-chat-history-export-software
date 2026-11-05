"""多选模型测试。"""
from __future__ import annotations

from types import SimpleNamespace
from datetime import datetime

import pytest

from qqexport import selection


def msgs(*ts):
    return [SimpleNamespace(ts=x) for x in ts]


def test_range_bounds_are_inclusive():
    data = msgs(100, 200, 300)
    assert selection.indices_in_range(data, 200, 300) == {1, 2}
    assert selection.indices_in_range(data, 100, 200) == {0, 1}
    assert selection.indices_in_range(data, None, 200) == {0, 1}
    assert selection.indices_in_range(data, 300, None) == {2}
    assert selection.indices_in_range(data, None, None) == {0, 1, 2}


def test_toggle_and_all():
    chosen: set[int] = set()
    selection.toggle(chosen, 2)
    assert chosen == {2}
    selection.toggle(chosen, 2)
    assert chosen == set()
    chosen.update(selection.all_indices(3))
    assert selection.is_all(chosen, 3)
    assert not selection.is_all(set(), 3)


def test_local_day_bounds_and_unlimited():
    start = selection.day_timestamp("2026-11-05")
    end = selection.day_timestamp("2026-11-05", end=True)
    assert datetime.fromtimestamp(start) == datetime(2026, 11, 5, 0, 0, 0)
    assert datetime.fromtimestamp(end) == datetime(2026, 11, 5, 23, 59, 59)
    assert selection.day_timestamp("2024-02-29") is not None
    assert selection.day_timestamp("") is None
    assert selection.day_timestamp(" ", end=True) is None


@pytest.mark.parametrize("text", ["2026-02-29", "2026-11-31", "2026-1-1", "bad"])
def test_invalid_day_is_rejected(text):
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        selection.day_timestamp(text)
