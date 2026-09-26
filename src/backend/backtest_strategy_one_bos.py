"""Causal Strategy 1 BOS cursor on V7's pinned completed-second read lane.

The cursor is read-only. It consumes the same arte.bars_v1 rows already fetched
for V7 catch-up, plus producer-certified pivot intervals. It does not derive
swings, create bars, write a market product, or infer intrabucket trade order.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from src.backend.backtest_strategy_one_pivot_store import CertifiedPivotPlan
from src.trading_runtime.strategy_one_bos import (
    BosBreak, BosObservation, ConfirmedPivot, observe_completed_bos,
)


@dataclass(frozen=True, slots=True)
class BosSnapshot:
    ticker: str
    as_of_boundary_ms: int
    open_break: BosBreak | None
    visible_pivots: tuple[ConfirmedPivot, ...]


class StrategyOneBosCursor:
    """O(source seconds + pivot changes), with no per-candidate rescan."""

    def __init__(self, plan: CertifiedPivotPlan) -> None:
        if not isinstance(plan, CertifiedPivotPlan):
            raise TypeError("Strategy 1 BOS requires certified pivot intervals")
        self._timelines = {ticker: plan.timeline(ticker)
                           for ticker, _ in plan.intervals}
        if len(self._timelines) != len(plan.intervals):
            raise ValueError("Strategy 1 BOS pivot plan duplicates a ticker")
        self._states: dict[str, BosObservation] = {}
        self._visible: dict[str, tuple[ConfirmedPivot, ...]] = {}
        self._last_clock: dict[str, int] = {}

    def _at(self, ticker: str, boundary_ms: int) -> tuple[ConfirmedPivot, ...]:
        timeline = self._timelines.get(ticker)
        if timeline is None:
            raise ValueError("Strategy 1 BOS ticker lacks certified pivots")
        prior = self._last_clock.get(ticker, 0)
        if (type(boundary_ms) is not int or boundary_ms < prior
                or not 0 < boundary_ms <= 57_600_000
                or boundary_ms % 100):
            raise ValueError("Strategy 1 BOS clock is not causal")
        if boundary_ms > prior:
            self._visible[ticker] = timeline.at(boundary_ms)
            self._last_clock[ticker] = boundary_ms
        return self._visible[ticker]

    def observe_second(self, ticker: str, row: Mapping[str, Any],
                       boundary_ms: int) -> None:
        """V7 callback: one pinned 1s row, including non-price-bearing rows."""
        if (not isinstance(row, Mapping) or row.get("ticker") != ticker
                or row.get("resolution_ms") != 1_000
                or type(boundary_ms) is not int or boundary_ms % 1_000
                or boundary_ms <= self._last_clock.get(ticker, 0)):
            raise ValueError("Strategy 1 BOS second differs from pinned source")
        pivots = self._at(ticker, boundary_ms)
        if int(row.get("price_valid") or 0) != 1:
            return
        bar = {"boundary_ms": boundary_ms, "resolution_ms": 1_000,
               "price_valid": 1, "close_int": row.get("close_int")}
        state, _ = observe_completed_bos(
            self._states.get(ticker, BosObservation()), bar,
            confirmed_pivots=pivots)
        self._states[ticker] = state

    def snapshot(self, ticker: str, *, boundary_ms: int) -> BosSnapshot:
        """Project a candidate's completed as-of gate, even at a 100ms clock."""
        pivots = self._at(ticker, boundary_ms)
        state = self._states.get(ticker)
        if state is not None and state.boundary_ms > boundary_ms:
            raise ValueError("Strategy 1 BOS consumed a future second")
        return BosSnapshot(ticker, boundary_ms,
                           state.open_break if state is not None else None,
                           pivots)
