"""Causal Strategy 1 work scheduling over sparse entries and active symbols.

The producer-certified candidate lane is immutable. Only the portfolio/OMS
coordinator may activate an order/position ticker. This scheduler never submits
orders, fabricates market rows, queries events, or writes market products. Its
output explicitly puts completed broker liquidity before a same-boundary entry
decision; the broker still enforces new-order activation delay.
"""
from __future__ import annotations

from dataclasses import dataclass
from contextlib import closing
from heapq import heappop, heappush
from typing import Any, Callable, Iterator, Mapping

from src.backend.backtest_market_data import (
    CertifiedMarketDayPlan, iter_market_boundary_groups,
    iter_market_day_rows, project_market_day_plan,
)
from src.backend.backtest_liquidity_price import PriceLevelPlan


MarketGroup = tuple[int, Mapping[int, Mapping]]
MarketSource = Callable[[str, int], Iterator[MarketGroup]]

# The sparse candidate projection and the active-ticker projection are two
# SELECT-only views of the same certified 100 ms product. Every shared field
# used for entry, broker matching, or market identity must agree exactly.
_SHARED_CANDIDATE_FIELDS = (
    "session_date", "ticker", "boundary_ms", "resolution_ms",
    "close_int", "low_int", "price_valid", "extremes_valid",
    "bid_int", "ask_int", "quote_valid", "quote_timestamp_us",
    "execution_vwap", "cumulative_volume", "cumulative_notional",
    "indicator_resolution_ms", "macd_line", "macd_signal",
    "previous_close",
)


def persisted_active_market_source(
    plan: CertifiedMarketDayPlan, *, price_plan: PriceLevelPlan,
    through_boundary_ms: int, client_factory: Callable[[], Any],
) -> MarketSource:
    """Open one SELECT-only ticker stream only while its financial state lives."""
    if (len(plan.sessions) != 1 or not isinstance(price_plan, PriceLevelPlan)
            or type(through_boundary_ms) is not int
            or not 0 < through_boundary_ms <= 57_600_000
            or through_boundary_ms % 100 or not callable(client_factory)):
        raise ValueError("Active Strategy 1 source lacks a certified fixed session")

    def source(ticker: str, after_boundary_ms: int) -> Iterator[MarketGroup]:
        if ticker not in plan.tickers or type(after_boundary_ms) is not int \
                or not 0 <= after_boundary_ms <= through_boundary_ms:
            raise ValueError("Active Strategy 1 source is outside certified scope")
        if after_boundary_ms == through_boundary_ms:
            return
        scoped = project_market_day_plan(plan, (ticker,))
        prices = price_plan.projected(scoped)
        reader = client_factory()
        if reader is None or not callable(getattr(reader, "close", None)):
            raise TypeError("Active Strategy 1 source needs a closable read client")
        with closing(reader):
            rows = iter_market_day_rows(
                scoped, client=reader, after_boundary_ms=after_boundary_ms,
                through_boundary_ms=through_boundary_ms, price_plan=prices)
            try:
                for day, boundary, symbol, resolutions in iter_market_boundary_groups(rows):
                    if day != plan.sessions[0] or symbol != ticker:
                        raise ValueError("Active Strategy 1 market row changed ticker scope")
                    yield boundary, resolutions
            finally:
                rows.close()

    return source


@dataclass(frozen=True, slots=True)
class StrategyOneBoundaryWork:
    boundary_ms: int
    broker_rows: tuple[tuple[str, Mapping[int, Mapping]], ...]
    candidate_rows: tuple[Mapping, ...]


class StrategyOneBoundaryScheduler:
    """One deterministic clock for entry candidates and active market reads."""

    def __init__(self, *, session_date: str,
                 candidate_rows: Iterator[Mapping],
                 active_source: MarketSource) -> None:
        if not session_date or not callable(active_source):
            raise ValueError("Strategy 1 scheduler needs a session and active source")
        self.session_date = session_date
        self._candidates = candidate_rows
        self._active_source = active_source
        self._candidate: Mapping | None = None
        self._prior_candidate: tuple[int, str] | None = None
        self._active: dict[str, Iterator[MarketGroup]] = {}
        self._active_prior: dict[str, int] = {}
        self._generation: dict[str, int] = {}
        self._heads: list[tuple[int, str, int, Mapping[int, Mapping]]] = []
        self._exhausted: set[str] = set()
        self._boundary_ms = 0
        self._closed = False
        self._advance_candidate()

    def _advance_candidate(self) -> None:
        row = next(self._candidates, None)
        if row is None:
            self._candidate = None
            return
        if not isinstance(row, Mapping):
            raise ValueError("Strategy 1 candidate market row is malformed")
        boundary = row.get("boundary_ms")
        ticker = row.get("ticker")
        key = (boundary, ticker)
        if (type(boundary) is not int or boundary <= 0 or boundary % 100
                or boundary > 57_600_000 or not isinstance(ticker, str)
                or not ticker or row.get("session_date") != self.session_date
                or row.get("resolution_ms") != 100
                or row.get("price_valid") != 1
                or row.get("indicator_resolution_ms") != 100
                or (self._prior_candidate is not None
                    and key <= self._prior_candidate)):
            raise ValueError("Strategy 1 candidates are not unique causal boundaries")
        self._prior_candidate = key
        self._candidate = row

    def _advance_active(self, ticker: str) -> None:
        source = self._active[ticker]
        try:
            boundary, resolutions = next(source)
        except StopIteration:
            # Source exhaustion is not a financial close. The coordinator
            # must still account for working orders and session-end policy.
            self._exhausted.add(ticker)
            close = getattr(source, "close", None)
            if close is not None:
                close()
            return
        if (type(boundary) is not int or boundary <= self._active_prior[ticker]
                or boundary > 57_600_000 or boundary % 100
                or not isinstance(resolutions, Mapping) or not resolutions
                or any(type(resolution) is not int or resolution < 100
                       or resolution % 100 or not isinstance(row, Mapping)
                       or row.get("session_date") != self.session_date
                       or row.get("ticker") != ticker
                       or row.get("boundary_ms") != boundary
                       or row.get("resolution_ms") != resolution
                       for resolution, row in resolutions.items())):
            raise ValueError("Active Strategy 1 source is not a completed ticker boundary")
        self._active_prior[ticker] = boundary
        heappush(self._heads, (boundary, ticker,
                               self._generation[ticker], resolutions))

    def activate(self, ticker: str) -> None:
        """Begin reading strictly after the last globally processed boundary."""
        if self._closed or not ticker or ticker in self._active:
            raise ValueError("Strategy 1 ticker cannot be activated twice")
        source = self._active_source(ticker, self._boundary_ms)
        if not hasattr(source, "__next__"):
            raise TypeError("Active Strategy 1 source must be a lazy iterator")
        self._active[ticker] = source
        self._active_prior[ticker] = self._boundary_ms
        self._generation[ticker] = self._generation.get(ticker, 0) + 1
        self._exhausted.discard(ticker)
        try:
            self._advance_active(ticker)
        except BaseException:
            self.deactivate(ticker)
            raise

    def deactivate(self, ticker: str) -> None:
        source = self._active.pop(ticker, None)
        self._active_prior.pop(ticker, None)
        self._exhausted.discard(ticker)
        if source is None:
            return
        close = getattr(source, "close", None)
        if close is not None:
            close()
        # Invalidate a prefetched head without disturbing another ticker.
        # pop_next ignores stale heads by checking active membership.

    def pop_next(self) -> StrategyOneBoundaryWork | None:
        if self._closed:
            raise RuntimeError("Strategy 1 scheduler is closed")
        while self._heads and (
            self._heads[0][1] not in self._active
            or self._heads[0][2] != self._generation[self._heads[0][1]]
        ):
            heappop(self._heads)
        candidate_at = (int(self._candidate["boundary_ms"])
                        if self._candidate is not None else None)
        active_at = self._heads[0][0] if self._heads else None
        if candidate_at is None and active_at is None:
            return None
        boundary = min(value for value in (candidate_at, active_at)
                       if value is not None)
        if boundary <= self._boundary_ms:
            raise ValueError("Strategy 1 scheduler moved backward")
        self._boundary_ms = boundary
        broker: dict[str, dict[int, Mapping]] = {}
        while self._heads and self._heads[0][0] == boundary:
            _, ticker, generation, resolutions = heappop(self._heads)
            if (ticker not in self._active
                    or generation != self._generation[ticker]):
                continue
            broker[ticker] = dict(resolutions)
            self._advance_active(ticker)
        candidates = []
        while (self._candidate is not None
               and self._candidate["boundary_ms"] == boundary):
            candidates.append(self._candidate)
            self._advance_candidate()
        for row in candidates:
            ticker = str(row["ticker"])
            by_resolution = broker.setdefault(ticker, {})
            existing = by_resolution.get(100)
            if existing is None:
                # A first entry candidate is itself a completed persisted
                # liquidity row. The broker must see its quote before OMS
                # evaluates an order at this same boundary.
                by_resolution[100] = row
            elif any(key in existing and key in row
                     and existing[key] != row[key]
                     for key in _SHARED_CANDIDATE_FIELDS) or (
                         "trade_count" in existing
                         and "volume_trade_count" in row
                         and existing["trade_count"] != row["volume_trade_count"]):
                raise ValueError("Active and candidate market rows disagree at boundary")
        return StrategyOneBoundaryWork(
            boundary, tuple(sorted(broker.items())), tuple(candidates))

    @property
    def exhausted_tickers(self) -> tuple[str, ...]:
        """Still-financially-active tickers with no more source rows."""
        return tuple(sorted(self._exhausted))

    def close(self) -> None:
        if self._closed:
            return
        for ticker in tuple(self._active):
            self.deactivate(ticker)
        self._heads.clear()
        close = getattr(self._candidates, "close", None)
        if close is not None:
            close()
        self._closed = True
