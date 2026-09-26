"""Normalize shared detector pivot snapshots for a producer-owned ARTE product.

STRATEGY CREATION RULES: Strategy 1 consumes these rows but never builds or
persists them during Backtest. A future strategy behavior change gets a new
strategy number. The producer must run the pinned shared detector over
certified completed one-second ARTE bars and publish coverage last.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Mapping, Sequence

from src.market_engine.structural_detector import VERSION


@dataclass(frozen=True, slots=True)
class PivotInterval:
    side: str
    price_int: int
    pivot_at_us: int
    confirmed_at_us: int
    valid_from_boundary_ms: int
    valid_to_boundary_ms: int | None

    @property
    def key(self) -> tuple[str, int, int, int]:
        return (self.side, self.price_int, self.pivot_at_us,
                self.confirmed_at_us)


def _event_us(value: object) -> int:
    if type(value) not in (int, float) or not isfinite(value) or value <= 0:
        raise ValueError("Structural pivot has a nonfinite event clock")
    micros = round(value * 1_000_000)
    if abs(value * 1_000_000 - micros) > 0.01:
        raise ValueError("Structural pivot clock exceeds microsecond precision")
    return micros


def snapshot_confirmed_pivots(row: Mapping) -> frozenset[tuple[str, int, int, int]]:
    """Replicate the legacy confirmed-pivot visibility filter, without JSON.

    Inactive/malformed evidence is not a pivot. A malformed detector envelope
    fails closed rather than falsely certifying an empty snapshot.
    """
    if not isinstance(row, Mapping) or row.get("contract") != VERSION:
        raise ValueError("Unknown structural detector contract")
    effective = _event_us(row.get("effective_at"))
    result: set[tuple[str, int, int, int]] = set()
    for field in ("local_swings", "confirmed_swings"):
        entries = row.get(field)
        if not isinstance(entries, (list, tuple)):
            raise ValueError("Structural pivot snapshot lacks a swing set")
        for pivot in entries:
            if not isinstance(pivot, Mapping):
                raise ValueError("Structural pivot entry is malformed")
            if pivot.get("state", "active") != "active":
                continue
            side = pivot.get("side")
            if side in (1, "support"):
                side = "low"
            elif side in (-1, "resistance"):
                side = "high"
            else:
                continue
            price = pivot.get("price")
            if type(price) not in (int, float) or not isfinite(price) or price <= 0:
                continue
            price_int = round(price * 10_000)
            if price_int <= 0 or abs(price * 10_000 - price_int) > 0.0001:
                raise ValueError("Structural pivot price exceeds source precision")
            try:
                occurred = _event_us(pivot.get("pivot_at"))
                confirmed = _event_us(pivot.get("confirmed_at"))
            except ValueError:
                continue
            if 0 < occurred < confirmed <= effective:
                result.add((side, price_int, occurred, confirmed))
    return frozenset(result)


class PivotIntervalBuilder:
    """Collapse repeated active snapshots into one interval per unique pivot.

    The producer calls observe for every completed one-second boundary. It
    must not skip a missing bucket: a gap ends all active intervals, and the
    next price-bearing bar starts a fresh detector episode.
    """

    def __init__(self) -> None:
        self._last_boundary = 0
        self._active: dict[tuple[str, int, int, int], int] = {}
        self._closed: list[PivotInterval] = []

    @property
    def last_boundary_ms(self) -> int:
        return self._last_boundary

    def observe(self, boundary_ms: int, row: Mapping) -> None:
        if (type(boundary_ms) is not int or boundary_ms <= self._last_boundary
                or boundary_ms % 1_000):
            raise ValueError("Pivot product needs ordered completed 1s boundaries")
        visible = snapshot_confirmed_pivots(row)
        if self._last_boundary and boundary_ms != self._last_boundary + 1_000:
            self._close_all(self._last_boundary + 1_000)
        for key in sorted(set(self._active) - visible):
            self._closed.append(PivotInterval(
                *key, self._active.pop(key), boundary_ms))
        for key in visible - set(self._active):
            self._active[key] = boundary_ms
        self._last_boundary = boundary_ms

    def _close_all(self, boundary_ms: int) -> None:
        for key, start in sorted(self._active.items()):
            self._closed.append(PivotInterval(*key, start, boundary_ms))
        self._active.clear()

    def finish(self) -> tuple[PivotInterval, ...]:
        if not self._last_boundary:
            raise ValueError("Pivot product has no completed source bar")
        result = self._closed + [PivotInterval(*key, start, None)
                                 for key, start in sorted(self._active.items())]
        return tuple(sorted(result, key=lambda item: (
            item.valid_from_boundary_ms, item.key,
            item.valid_to_boundary_ms or 0)))
