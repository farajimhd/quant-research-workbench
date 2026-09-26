"""Compact as-of cursor over certified Strategy 1 pivot validity intervals.

STRATEGY CREATION RULES: only producer-certified ARTE intervals enter this
cursor. It is a read-only projection, not a detector, market-data fallback,
order authority, or journal writer. Advance monotonically at completed clocks.
"""
from __future__ import annotations

from datetime import date
from typing import Sequence

from src.backend.backtest_market_data import market_day_boundary
from .strategy_one_bos import ConfirmedPivot
from .strategy_one_pivot_product import PivotInterval, interval_content_hash


class PivotTimeline:
    """O(changed intervals) advance with an active-only pivot projection."""

    def __init__(self, *, session_date: str,
                 intervals: Sequence[PivotInterval]) -> None:
        day = date.fromisoformat(session_date)
        ordered = tuple(intervals)
        interval_content_hash(ordered)
        origin_us = round(market_day_boundary(day, 0).timestamp() * 1_000_000)
        encoded = []
        for index, item in enumerate(ordered):
            occurred_us = item.pivot_at_us - origin_us
            confirmed_us = item.confirmed_at_us - origin_us
            if (not 0 < occurred_us < confirmed_us
                    or confirmed_us > item.valid_from_boundary_ms * 1_000
                    or occurred_us % 1_000 or confirmed_us % 1_000):
                raise ValueError("Pivot interval has noncausal session clocks")
            identity = (f"{item.side}:{item.price_int}:"
                        f"{item.pivot_at_us}:{item.confirmed_at_us}")
            encoded.append((index, item, ConfirmedPivot(
                identity, item.side, item.price_int,
                occurred_us // 1_000, confirmed_us // 1_000)))
        self._starts = tuple(encoded)
        self._ends = tuple(sorted(
            ((item.valid_to_boundary_ms, index)
             for index, item, _ in encoded
             if item.valid_to_boundary_ms is not None),
            key=lambda pair: pair))
        self._next_start = self._next_end = 0
        self._active: dict[int, ConfirmedPivot] = {}
        self._boundary_ms = 0

    def at(self, boundary_ms: int) -> tuple[ConfirmedPivot, ...]:
        if (type(boundary_ms) is not int or boundary_ms <= self._boundary_ms
                or boundary_ms > 57_600_000 or boundary_ms % 100):
            raise ValueError("Pivot cursor needs increasing completed boundaries")
        while (self._next_start < len(self._starts)
               and self._starts[self._next_start][1].valid_from_boundary_ms
               <= boundary_ms):
            index, _, pivot = self._starts[self._next_start]
            self._active[index] = pivot
            self._next_start += 1
        while (self._next_end < len(self._ends)
               and self._ends[self._next_end][0] <= boundary_ms):
            self._active.pop(self._ends[self._next_end][1], None)
            self._next_end += 1
        self._boundary_ms = boundary_ms
        return tuple(sorted(self._active.values(), key=lambda pivot: (
            pivot.pivot_boundary_ms, pivot.confirmed_boundary_ms,
            pivot.pivot_id)))
