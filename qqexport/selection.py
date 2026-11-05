"""多选模型的纯函数，便于脱离 Tk 测试。"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, time


def day_timestamp(text: str, *, end: bool = False) -> int | None:
    """本地日期的包含式日界；GUI 与 CLI 共用，不指定时不限制。"""
    text = text.strip()
    if not text:
        return None
    try:
        day = datetime.strptime(text, "%Y-%m-%d").date()
        if day.isoformat() != text:
            raise ValueError
        bound = datetime.combine(day, time(23, 59, 59) if end else time.min)
        return int(bound.timestamp())
    except (ValueError, OverflowError, OSError):
        raise ValueError("日期应为有效的本地日期 YYYY-MM-DD") from None


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
