"""多选模型的纯函数，便于脱离 Tk 测试。"""
from __future__ import annotations

from collections.abc import Iterable


def indices_in_range(messages: Iterable, start: int | None,
                     end: int | None) -> set[int]:
    return {
        i for i, m in enumerate(messages)
        if (start is None or m.ts >= start)
        and (end is None or m.ts <= end)
    }


def toggle(selected: set[int], idx: int) -> None:
    if idx in selected:
        selected.discard(idx)
    else:
        selected.add(idx)


def all_indices(count: int) -> set[int]:
    return set(range(count))


def is_all(selected: set[int], count: int) -> bool:
    return count > 0 and len(selected) == count
