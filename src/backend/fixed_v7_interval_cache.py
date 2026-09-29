"""Causal Strategy 1 V7 cache backed only by certified ARTE scalar intervals.

No level fitting, checkpoint decode, producer import, or market write occurs
here. Completed 1s bars still drive BOS and resistance observations, while
sealed interval geometry supplies the strategy-facing V7 projection.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from datetime import date, datetime
from types import MappingProxyType
from typing import Any, Callable, Mapping, Sequence

from src.backend.backtest_market_data import (
    CertifiedMarketDayPlan, SESSION_OPEN_OFFSET_MS,
    iter_persisted_v7_seconds, market_day_boundary,
)
from src.backend.backtest_strategy_one_v7_interval_store import CertifiedV7IntervalPlan


class FixedV7IntervalCache:
    """Load only observed completed seconds; look up precomputed V7 levels."""

    def __init__(self, *, market_plan: CertifiedMarketDayPlan,
                 interval_plan: CertifiedV7IntervalPlan, session: date,
                 client: Any,
                 precomputed_entry_facts: bool = False,
                 observe_completed_second: Callable[
                     [str, Mapping[str, Any], int], None] | None = None) -> None:
        if (not isinstance(market_plan, CertifiedMarketDayPlan)
                or not isinstance(interval_plan, CertifiedV7IntervalPlan)
                or not isinstance(session, date)
                or market_plan.sessions != (session.isoformat(),)
                or interval_plan.source_build_id != market_plan.build_id
                or interval_plan.session_date != session.isoformat()
                or tuple(unit.ticker for unit in interval_plan.coverage)
                   != market_plan.tickers
                or not callable(getattr(client, "iter_json_each_row", None))
                or type(precomputed_entry_facts) is not bool
                or precomputed_entry_facts and observe_completed_second is not None
                or observe_completed_second is not None
                and not callable(observe_completed_second)):
            raise ValueError("V7 interval cache needs one pinned certified session")
        self.market_plan = market_plan
        self.interval_plan = interval_plan
        self.session = session
        self.client = client
        self._observe_completed_second = observe_completed_second
        self._precomputed_entry_facts = precomputed_entry_facts
        self._coverage = {row.ticker: row for row in interval_plan.coverage}
        self._clocks = {ticker: values for ticker, values in interval_plan.valid_seconds}
        self._last_loaded_ms: dict[str, int] = {}
        self._last_observed_ms: dict[str, int] = {}
        self._last_completed_price: dict[str, Mapping[str, Any]] = {}
        # Geometry changes only on a certified valid completed second. Cache
        # immutable rows so repeated 100ms decisions cannot mutate evidence
        # or pay for another copy of unchanged level dictionaries.
        self._level_projection: dict[
            str, tuple[int, tuple[Mapping[str, object], ...]]
        ] = {}

    @property
    def prefetches_seconds(self) -> bool:
        return False

    def preload_seeds(self, tickers: Sequence[str], *,
                      client_factory: Callable[[], Any],
                      max_workers: int = 4) -> int:
        """Persisted geometry has no decoded seed to preload."""
        if (not tickers or not set(tickers) <= self._coverage.keys()
                or len(set(tickers)) != len(tickers)
                or not callable(client_factory)
                or not 1 <= max_workers <= 16):
            raise ValueError("V7 interval preload scope is invalid")
        return 0

    def _boundary_ms(self, at: datetime) -> int:
        if at.tzinfo is None:
            raise ValueError("V7 interval boundary needs a timezone")
        start = market_day_boundary(self.session, 0)
        elapsed = at - start
        boundary = (elapsed.days * 86_400_000 + elapsed.seconds * 1_000
                    + elapsed.microseconds // 1_000)
        if (at.astimezone(start.tzinfo).date() != self.session
                or elapsed.microseconds % 1_000
                or not 0 <= boundary <= 57_600_000):
            raise ValueError("V7 interval boundary is outside the session")
        return boundary

    def has_stream(self, ticker: str) -> bool:
        return ticker in self._last_loaded_ms

    def strategy_one_ready_without_read(self, ticker: str, *,
                                        as_of: datetime) -> bool:
        if ticker not in self._coverage:
            raise ValueError("V7 ticker is outside sealed interval coverage")
        completed = self._boundary_ms(as_of) // 1_000 * 1_000
        return self._last_loaded_ms.get(ticker) == completed

    def last_completed_price_second(self, ticker: str) -> Mapping[str, Any] | None:
        if ticker not in self._coverage:
            raise ValueError("V7 ticker is outside sealed interval coverage")
        return self._last_completed_price.get(ticker)

    def _observe(self, ticker: str, row: Mapping[str, Any], boundary: int) -> None:
        if (row.get("ticker") != ticker or int(row["resolution_ms"]) != 1_000
                or boundary <= self._last_observed_ms.get(ticker, 0)
                or boundary % 1_000):
            raise ValueError("V7 completed second is out of order or scope")
        valid = bool(int(row.get("price_valid") or 0)
                     and int(row.get("extremes_valid") or 0))
        clocks = self._clocks[ticker]
        at = bisect_left(clocks, boundary)
        if valid != (at < len(clocks) and clocks[at] == boundary):
            raise RuntimeError("V7 sealed validity clock differs from pinned bar")
        if valid:
            self._last_completed_price[ticker] = {
                **row, "boundary_ms": boundary,
                "session_date": self.session.isoformat()}
        else:
            self._last_completed_price.pop(ticker, None)
        if self._observe_completed_second is not None:
            self._observe_completed_second(ticker, row, boundary)
        self._last_observed_ms[ticker] = boundary

    def _load_to(self, ticker: str, *, through_ms: int) -> None:
        if ticker not in self._coverage or through_ms % 1_000:
            raise ValueError("V7 interval catch-up scope is invalid")
        after = self._last_loaded_ms.get(ticker, 0)
        if through_ms <= after:
            return
        if self._precomputed_entry_facts:
            # Producer-certified entry/BOS facts replace historical BOS replay.
            # Only the exact last completed second can initialize activation's
            # prior-price observation. Financially active seconds still arrive
            # in order from the separate pinned market tape.
            clocks = self._clocks[ticker]
            at = bisect_left(clocks, through_ms)
            if at < len(clocks) and clocks[at] == through_ms:
                rows = tuple(iter_persisted_v7_seconds(
                    self.market_plan, session_date=self.session.isoformat(),
                    ticker=ticker, after_boundary_ms=through_ms - 1_000,
                    through_boundary_ms=through_ms, client=self.client))
                if (len(rows) != 1 or type(rows[0].get("bucket_index")) is not int
                        or (rows[0]["bucket_index"] + 1) * 1_000
                           - SESSION_OPEN_OFFSET_MS != through_ms):
                    raise RuntimeError("V7 last completed price second is missing or duplicate")
                self._observe(ticker, rows[0], through_ms)
            else:
                self._last_completed_price.pop(ticker, None)
            self._last_loaded_ms[ticker] = through_ms
            return
        observed_valid: list[int] = []
        for row in iter_persisted_v7_seconds(
                self.market_plan, session_date=self.session.isoformat(),
                ticker=ticker, after_boundary_ms=after,
                through_boundary_ms=through_ms, client=self.client):
            boundary = ((int(row["bucket_index"]) + 1) * 1_000
                        - SESSION_OPEN_OFFSET_MS)
            if not after < boundary <= through_ms:
                raise ValueError("V7 interval source crossed its causal boundary")
            self._observe(ticker, row, boundary)
            if int(row.get("price_valid") or 0) and int(row.get("extremes_valid") or 0):
                observed_valid.append(boundary)
        clocks = self._clocks[ticker]
        expected = clocks[bisect_right(clocks, after):bisect_right(clocks, through_ms)]
        if tuple(observed_valid) != expected:
            raise RuntimeError("V7 sealed validity clocks omit pinned 1s bars")
        self._last_loaded_ms[ticker] = through_ms

    def advance_seconds(self, rows: Sequence[Mapping[str, Any]], *,
                        at: datetime) -> None:
        boundary = self._boundary_ms(at)
        if boundary % 1_000:
            raise ValueError("V7 interval advance needs completed second")
        for row in rows:
            ticker = str(row.get("ticker") or "")
            if ticker not in self._last_loaded_ms:
                continue
            prior = self._last_loaded_ms[ticker]
            if boundary <= prior:
                raise ValueError("V7 interval second duplicated")
            if boundary - prior > 1_000:
                self._load_to(ticker, through_ms=boundary - 1_000)
            self._observe(ticker, row, boundary)
            self._last_loaded_ms[ticker] = boundary

    def strategy_one_levels(self, ticker: str, *,
                            as_of: datetime) -> tuple[Mapping[str, Any], ...]:
        boundary = self._boundary_ms(as_of)
        completed = boundary // 1_000 * 1_000
        self._load_to(ticker, through_ms=completed)
        clocks = self._clocks[ticker]
        index = bisect_right(clocks, boundary) - 1
        input_ms = clocks[index] if index >= 0 else 0
        if boundary - input_ms > 1_000:
            return ()
        cached = self._level_projection.get(ticker)
        if cached is None or cached[0] != input_ms:
            rows = self.interval_plan.levels(ticker, boundary_ms=boundary)
            cached = (input_ms, tuple(MappingProxyType(row) for row in rows))
            self._level_projection[ticker] = cached
        return cached[1]
