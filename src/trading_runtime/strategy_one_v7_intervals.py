"""Compact causal Strategy 1 V7 geometry derived by a separate producer.

STRATEGY CREATION RULES: these are scalar, completed-second derivatives of
the pinned V7 stream. Backtest may only read a certified persisted product;
it must never call this projector to repair a missing product or write arte.
No JSON checkpoint or per-100-ms level snapshot is part of this contract.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
import json
from math import isfinite
from struct import pack, unpack
from typing import Mapping, Sequence

from src.market_engine.derived_trade_policy import POLICY
from src.market_engine.streaming_level_book import VERSION
from src.trading_runtime.strategy_one_v7 import PROVISIONAL_SEED_POLICY


SESSION_MS = 57_600_000


@dataclass(frozen=True, slots=True)
class V7LevelInterval:
    level_id: str
    ordinal: int
    valid_from_ms: int
    valid_to_ms: int
    lower: float
    upper: float
    role: str
    transition_from: str
    confirmed_at_ms: int
    historical: bool


def _bits(value: float) -> int:
    return unpack("<Q", pack("<d", value))[0]


def clock_hash(clocks: tuple[int, ...]) -> str:
    return sha256(json.dumps(clocks, sort_keys=True, separators=(",", ":"),
                             allow_nan=False).encode()).hexdigest()


def interval_hash(intervals: tuple[V7LevelInterval, ...]) -> str:
    values = tuple((row.level_id, row.ordinal, row.valid_from_ms,
                    row.valid_to_ms, _bits(row.lower), _bits(row.upper),
                    row.role, row.transition_from, row.confirmed_at_ms,
                    row.historical) for row in intervals)
    return sha256(json.dumps(values, sort_keys=True, separators=(",", ":"),
                             allow_nan=False).encode()).hexdigest()


def _geometry(row: Mapping) -> tuple[str, float, float, str, str, int, bool]:
    identity = row.get("unified_level_id")
    lower, upper = row.get("lower"), row.get("upper")
    role = row.get("role")
    transition = row.get("transition_from") or ""
    confirmed = row.get("confirmed_at_ms")
    historical = row.get("historical")
    if (not isinstance(identity, str) or not identity
            or row.get("book_version") != VERSION
            or type(lower) not in (int, float)
            or type(upper) not in (int, float)
            or not isfinite(lower) or not isfinite(upper)
            or not 0 < lower <= upper
            or role not in {"support", "resistance", "transition"}
            or transition not in {"", "support", "resistance"}
            or type(confirmed) not in (int, float)
            or not isfinite(confirmed) or confirmed <= 0
            or confirmed != int(confirmed)
            or type(historical) is not bool):
        raise ValueError("V7 derivative has invalid scalar level geometry")
    return (identity, float(lower), float(upper), role, transition,
            int(confirmed), historical)


class V7IntervalProjector:
    """Emit one interval per level change, plus exact valid input clocks."""

    def __init__(self) -> None:
        self._last_boundary = -1
        self._active: dict[str, V7LevelInterval] = {}
        self._closed: list[V7LevelInterval] = []
        self._valid_seconds: list[int] = []

    def observe(self, *, boundary_ms: int, levels: Sequence[Mapping],
                valid_completed_second: bool) -> None:
        if (type(boundary_ms) is not int or not 0 <= boundary_ms <= SESSION_MS
                or boundary_ms % 1_000 or boundary_ms <= self._last_boundary
                or not isinstance(levels, (list, tuple))
                or type(valid_completed_second) is not bool
                or (boundary_ms == 0 and valid_completed_second)):
            raise ValueError("V7 derivative needs ordered completed-second clocks")
        if not valid_completed_second and boundary_ms != 0:
            # An invalid or absent 1s bar cannot change V7 geometry. It also
            # cannot refresh the input clock used by the 100ms consumer.
            if levels:
                raise ValueError("Invalid V7 second cannot publish geometry")
            self._last_boundary = boundary_ms
            return
        if valid_completed_second:
            self._valid_seconds.append(boundary_ms)
        next_rows: dict[str, V7LevelInterval] = {}
        for ordinal, source in enumerate(levels):
            identity, lower, upper, role, transition, confirmed, historical = _geometry(source)
            if identity in next_rows:
                raise ValueError("V7 derivative repeated a level identity")
            next_rows[identity] = V7LevelInterval(
                identity, ordinal, boundary_ms, SESSION_MS + 1, lower, upper,
                role, transition, confirmed, historical)
        for identity, current in self._active.items():
            proposed = next_rows.get(identity)
            if proposed is None or replace(proposed, valid_from_ms=current.valid_from_ms) != current:
                self._closed.append(replace(current, valid_to_ms=boundary_ms))
            else:
                next_rows[identity] = current
        self._active = next_rows
        self._last_boundary = boundary_ms

    def observe_unchanged(self, *, boundary_ms: int) -> None:
        """Refresh a valid second without reprojecting an unchanged V7 book."""
        if (self._last_boundary < 0 or type(boundary_ms) is not int
                or not 0 < boundary_ms <= SESSION_MS or boundary_ms % 1_000
                or boundary_ms <= self._last_boundary):
            raise ValueError("V7 unchanged projection needs a later valid second")
        self._valid_seconds.append(boundary_ms)
        self._last_boundary = boundary_ms

    def finish(self) -> tuple[tuple[int, ...], tuple[V7LevelInterval, ...]]:
        if self._last_boundary < 0:
            raise ValueError("V7 derivative has no initial seed projection")
        rows = (*self._closed, *self._active.values())
        return tuple(self._valid_seconds), tuple(sorted(
            rows, key=lambda row: (row.valid_from_ms, row.level_id)))


def levels_at(*, boundary_ms: int, seed_policy: str,
              valid_seconds: Sequence[int],
              intervals: Sequence[V7LevelInterval]) -> tuple[dict[str, object], ...]:
    """Read completed geometry at a 100ms boundary, without future access."""
    return _levels_at(boundary_ms=boundary_ms, seed_policy=seed_policy,
                      valid_seconds=valid_seconds, intervals=intervals,
                      clocks_certified=False)


def _levels_at(*, boundary_ms: int, seed_policy: str,
               valid_seconds: Sequence[int],
               intervals: Sequence[V7LevelInterval],
               clocks_certified: bool) -> tuple[dict[str, object], ...]:
    """Certified plans validate clocks once; ad-hoc callers validate here."""
    from bisect import bisect_right

    if (seed_policy not in {POLICY, PROVISIONAL_SEED_POLICY}
            or type(boundary_ms) is not int or not 0 <= boundary_ms <= SESSION_MS
            or boundary_ms % 100):
        raise ValueError("V7 derivative lookup needs a 100ms session boundary")
    if not clocks_certified and (any(
            type(value) is not int or value <= 0 or value % 1_000
            for value in valid_seconds)
            or any(left >= right for left, right in zip(valid_seconds,
                                                        valid_seconds[1:]))):
        raise ValueError("V7 derivative valid clocks are not ordered")
    index = bisect_right(valid_seconds, boundary_ms) - 1
    input_ms = valid_seconds[index] if index >= 0 else 0
    if boundary_ms - input_ms > 1_000:
        return ()
    rows = tuple(row for row in intervals
                 if row.valid_from_ms <= input_ms < row.valid_to_ms)
    if len({row.level_id for row in rows}) != len(rows):
        raise ValueError("V7 derivative has overlapping level intervals")
    return tuple({
        "unified_level_id": row.level_id,
        "lower": row.lower, "upper": row.upper,
        "role": row.role, "side": (1 if row.role == "support" else
                                     -1 if row.role == "resistance" else 0),
        "transition_from": row.transition_from or None,
        "confirmed_at_ms": row.confirmed_at_ms,
        "historical": row.historical,
        "book_version": VERSION,
        "input_policy": POLICY,
        "seed_input_policy": seed_policy,
    } for row in sorted(rows, key=lambda item: item.ordinal))
