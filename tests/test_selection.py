"""多选模型测试。"""
from __future__ import annotations

from types import SimpleNamespace

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
