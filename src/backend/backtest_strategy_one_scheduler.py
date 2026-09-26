"""Causal Strategy 1 work scheduling over sparse entries and active symbols.

The producer-certified candidate lane is immutable. Only the portfolio/OMS
coordinator may activate an order/position ticker. This scheduler never submits
orders, fabricates market rows, queries events, or writes market products. Its
output explicitly puts completed broker liquidity before a same-boundary entry
decision; the broker still enforces new-order activation delay.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from contextlib import closing
from heapq import heappop, heappush
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Iterator, Mapping

from src.backend.backtest_market_data import (
    CertifiedMarketDayPlan, iter_market_boundary_groups,
    iter_market_day_rows, project_market_day_plan,
)
from src.backend.backtest_liquidity_price import PriceLevelPlan
from src.backend.backtest_strategy_one_market import StrategyOneDecisionCandidate
from src.backend.backtest_strategy_one_market import (
    attach_sparse_candidate_evidence, load_sparse_candidate_market,
)
from src.backend.backtest_strategy_one_candidate_store import CertifiedCandidatePlan
from src.backend.backtest_strategy_one_activation import (
    CertifiedActivationPlan, StrategyOneActivation,
)

if TYPE_CHECKING:
    from src.backend.backtest_strategy_one_static_gate import StrategyOneStaticGate


MarketGroup = tuple[int, Mapping[int, Mapping]]
MarketSource = Callable[[str, int], Iterator[MarketGroup]]


def build_certified_strategy_one_scheduler(
    plan: CertifiedMarketDayPlan, candidates: CertifiedCandidatePlan, *,
    activations: CertifiedActivationPlan,
    price_plan: PriceLevelPlan, through_boundary_ms: int,
    client_factory: Callable[[], Any], max_workers: int = 4,
    max_candidate_rows: int = 250_000,
    activation_source_candidates: CertifiedCandidatePlan | None = None,
) -> StrategyOneBoundaryScheduler:
    """Build the sparse causal tape solely from certified arte products."""
    if (len(plan.sessions) != 1 or plan.execution_interval.kind != "fixed"
            or plan.execution_interval.milliseconds != 100
            or candidates.source_build_id != plan.build_id
            or not isinstance(activations, CertifiedActivationPlan)
            or len(activations.token) != 64
            or type(through_boundary_ms) is not int
            or not 0 < through_boundary_ms <= 57_600_000
            or through_boundary_ms % 100):
        raise ValueError("Strategy 1 scheduler needs one pinned 100ms session")
    source_candidates = activation_source_candidates or candidates
    if (source_candidates.source_build_id != candidates.source_build_id
            or (activation_source_candidates is not None
                and not {(prepared.ticker, int(boundary), int(start))
                         for prepared in candidates.prepared
                         for boundary, start in zip(prepared.boundary_ms,
                                                    prepared.episode_start_ms)}
                <= {(prepared.ticker, int(boundary), int(start))
                    for prepared in source_candidates.prepared
                    for boundary, start in zip(prepared.boundary_ms,
                                               prepared.episode_start_ms)})):
        raise ValueError("Strategy 1 pruned candidates differ from activation source")
    expected_activations = {
        (int(start), prepared.ticker)
        for prepared in source_candidates.prepared
        for start in prepared.episode_start_ms
    }
    actual_activations = {
        (item.boundary_ms, item.ticker) for item in activations.rows
    }
    if (expected_activations != actual_activations
            or len(actual_activations) != len(activations.rows)):
        raise ValueError("Strategy 1 activation schedule differs from candidates")
    rows = load_sparse_candidate_market(
        plan, candidates, price_plan=price_plan,
        client_factory=client_factory, max_workers=max_workers,
        max_rows=max_candidate_rows)
    paired = attach_sparse_candidate_evidence(rows, candidates.prepared)
    source = persisted_active_market_source(
        plan, price_plan=price_plan,
        through_boundary_ms=through_boundary_ms,
        client_factory=client_factory)
    return StrategyOneBoundaryScheduler(
        session_date=plan.sessions[0], candidate_rows=iter(paired),
        activation_rows=iter(activations.rows), active_source=source)

# Both SELECT-only paths use the same full 100 ms projection. Compare the
# complete row when they overlap: the broker also consumes size, high,
# eligible volume and execution price levels, not just strategy gate fields.


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
    candidate_rows: tuple[StrategyOneDecisionCandidate, ...]
    activation_rows: tuple[StrategyOneActivation, ...] = ()


class StrategyOneBoundaryScheduler:
    """One deterministic clock for entry candidates and active market reads."""

    def __init__(self, *, session_date: str,
                 candidate_rows: Iterator[StrategyOneDecisionCandidate],
                 activation_rows: Iterator[StrategyOneActivation] | None = None,
                 active_source: MarketSource) -> None:
        if not session_date or not callable(active_source):
            raise ValueError("Strategy 1 scheduler needs a session and active source")
        self.session_date = session_date
        self._candidates = candidate_rows
        self._activations = activation_rows or iter(())
        self._active_source = active_source
        self._candidate: StrategyOneDecisionCandidate | None = None
        self._activation: StrategyOneActivation | None = None
        self._prior_candidate: tuple[int, str] | None = None
        self._prior_activation: tuple[int, str] | None = None
        self._active: dict[str, Iterator[MarketGroup]] = {}
        self._active_prior: dict[str, int] = {}
        self._generation: dict[str, int] = {}
        self._heads: list[tuple[int, str, int, Mapping[int, Mapping]]] = []
        self._exhausted: set[str] = set()
        self._boundary_ms = 0
        self._closed = False
        self._advance_candidate()
        self._advance_activation()

    def _advance_activation(self) -> None:
        row = next(self._activations, None)
        if row is None:
            self._activation = None
            return
        if not isinstance(row, StrategyOneActivation):
            raise ValueError("Strategy 1 activation lacks typed source evidence")
        key = (row.boundary_ms, row.ticker)
        if (type(row.boundary_ms) is not int
                or not 0 < row.boundary_ms <= 57_600_000
                or row.boundary_ms % 100
                or not isinstance(row.ticker, str)
                or not row.ticker or row.ticker != row.ticker.upper()
                or type(row.price_int) is not int or row.price_int <= 0
                or self._prior_activation is not None
                and key <= self._prior_activation):
            raise ValueError("Strategy 1 activations are not unique causal boundaries")
        self._prior_activation = key
        self._activation = row

    def _advance_candidate(self) -> None:
        row = next(self._candidates, None)
        if row is None:
            self._candidate = None
            return
        if not isinstance(row, StrategyOneDecisionCandidate):
            raise ValueError("Strategy 1 candidate lacks paired closed-bar evidence")
        market = row.market_row
        evidence = row.evidence
        if not isinstance(market, Mapping):
            raise ValueError("Strategy 1 candidate market row is malformed")
        boundary = market.get("boundary_ms")
        ticker = market.get("ticker")
        key = (boundary, ticker)
        if (type(boundary) is not int or boundary <= 0 or boundary % 100
                or boundary > 57_600_000 or not isinstance(ticker, str)
                or not ticker or market.get("session_date") != self.session_date
                or market.get("resolution_ms") != 100
                or market.get("price_valid") != 1
                or market.get("indicator_resolution_ms") != 100
                or evidence.boundary_ms != boundary or evidence.ticker != ticker
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

    def reconcile_financial_tickers(self, tickers: tuple[str, ...]) -> None:
        """Follow broker-owned open orders and positions after a boundary.

        A failed new SELECT leaves the prior active set intact. Terminal
        broker orders alone must not keep a ticker on the market tape.
        """
        if (self._closed or not isinstance(tickers, tuple)
                or len(set(tickers)) != len(tickers)
                or any(not isinstance(ticker, str) or not ticker
                       or ticker != ticker.upper() for ticker in tickers)):
            raise ValueError("Active Strategy 1 financial ticker set is invalid")
        desired = set(tickers)
        added: list[str] = []
        try:
            for ticker in sorted(desired - self._active.keys()):
                self.activate(ticker)
                added.append(ticker)
        except BaseException:
            for ticker in reversed(added):
                self.deactivate(ticker)
            raise
        for ticker in sorted(self._active.keys() - desired):
            self.deactivate(ticker)

    @property
    def active_tickers(self) -> tuple[str, ...]:
        return tuple(sorted(self._active))

    def pop_next(self) -> StrategyOneBoundaryWork | None:
        if self._closed:
            raise RuntimeError("Strategy 1 scheduler is closed")
        while self._heads and (
            self._heads[0][1] not in self._active
            or self._heads[0][2] != self._generation[self._heads[0][1]]
        ):
            heappop(self._heads)
        candidate_at = (int(self._candidate.market_row["boundary_ms"])
                        if self._candidate is not None else None)
        activation_at = (self._activation.boundary_ms
                         if self._activation is not None else None)
        active_at = self._heads[0][0] if self._heads else None
        if candidate_at is None and active_at is None and activation_at is None:
            return None
        boundary = min(value for value in (candidate_at, active_at, activation_at)
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
               and self._candidate.market_row["boundary_ms"] == boundary):
            candidates.append(self._candidate)
            self._advance_candidate()
        activations = []
        while (self._activation is not None
               and self._activation.boundary_ms == boundary):
            activations.append(self._activation)
            self._advance_activation()
        for candidate in candidates:
            row = candidate.market_row
            ticker = str(row["ticker"])
            by_resolution = broker.setdefault(ticker, {})
            existing = by_resolution.get(100)
            if existing is None:
                # A first entry candidate is itself a completed persisted
                # liquidity row. The broker must see its quote before OMS
                # evaluates an order at this same boundary.
                by_resolution[100] = row
            elif dict(existing) != dict(row):
                raise ValueError("Active and candidate market rows disagree at boundary")
        return StrategyOneBoundaryWork(
            boundary, tuple(sorted(broker.items())), tuple(candidates),
            tuple(activations))

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
        close = getattr(self._activations, "close", None)
        if close is not None:
            close()
        self._closed = True


async def run_strategy_one_boundaries(
    scheduler: StrategyOneBoundaryScheduler, *,
    before_boundary: Callable[[StrategyOneBoundaryWork], Awaitable[None]] | None = None,
    process_broker_boundary: Callable[[StrategyOneBoundaryWork], Awaitable[None]],
    evaluate_ticker: Callable[[str, Mapping[int, Mapping],
                               StrategyOneDecisionCandidate | None], Awaitable[None]],
    financially_active_tickers: Callable[[], tuple[str, ...]],
    finish_boundary: Callable[[StrategyOneBoundaryWork], Awaitable[None]],
    observe_activation: Callable[[StrategyOneActivation], Awaitable[None]] | None = None,
    observe_completed_seconds: Callable[[StrategyOneBoundaryWork], Awaitable[None]] | None = None,
    static_gate: StrategyOneStaticGate | None = None,
) -> int:
    """One causal coordinator; market I/O cannot block the asyncio engine.

    All completed broker rows are applied before any candidate/active
    strategy evaluation at that boundary. Only broker-owned financial state
    keeps a ticker on the subsequent active tape. The caller owns portfolio,
    OMS, journal, and decisions; this function never manufactures events.
    """
    if not isinstance(scheduler, StrategyOneBoundaryScheduler) or any(
            not callable(callback) for callback in (
                process_broker_boundary, evaluate_ticker,
                financially_active_tickers, finish_boundary)):
        raise TypeError("Strategy 1 coordinator needs typed scheduler callbacks")
    if observe_completed_seconds is not None and not callable(observe_completed_seconds):
        raise TypeError("Strategy 1 completed-second observer must be callable")
    if before_boundary is not None and not callable(before_boundary):
        raise TypeError("Strategy 1 boundary control must be callable")
    from src.backend.backtest_strategy_one_static_gate import StrategyOneStaticGate
    if static_gate is not None and not isinstance(static_gate, StrategyOneStaticGate):
        raise TypeError("Strategy 1 coordinator needs a typed static gate")
    pending_gate = ({(fact.ticker, fact.boundary_ms): int(mask)
                     for fact, mask in zip(static_gate.facts, static_gate.rejection_mask)}
                    if static_gate is not None else None)
    if pending_gate is not None and len(pending_gate) != len(static_gate.facts):
        raise ValueError("Strategy 1 static gate repeats a candidate")
    count = 0
    try:
        initial = financially_active_tickers()
        if initial != scheduler.active_tickers:
            await asyncio.to_thread(scheduler.reconcile_financial_tickers, initial)
        while True:
            # Candidate-only boundaries are already resident typed rows. A
            # thread round-trip per 100 ms candidate would erase much of the
            # vectorized preparation gain. Active streams alone can perform
            # a ClickHouse fetch while advancing their prefetched head.
            active = scheduler.active_tickers
            if set(active) - set(scheduler.exhausted_tickers):
                work = await asyncio.to_thread(scheduler.pop_next)
            else:
                work = scheduler.pop_next()
            if work is None:
                if pending_gate:
                    raise ValueError("Strategy 1 static gate contains unseen candidates")
                remaining = financially_active_tickers()
                if remaining:
                    raise RuntimeError(
                        "Strategy 1 market stream ended with financially active tickers: "
                        + ", ".join(remaining))
                return count
            if before_boundary is not None:
                await before_boundary(work)
            candidates = {row.market_row["ticker"]: row
                          for row in work.candidate_rows}
            candidate_rejections = {}
            if pending_gate is not None:
                for ticker in candidates:
                    key = (ticker, work.boundary_ms)
                    if key not in pending_gate:
                        raise ValueError("Strategy 1 candidate lacks static gate evidence")
                    candidate_rejections[ticker] = pending_gate.pop(key)
            if work.broker_rows:
                # All completed ticker rows reach the broker together. OMS
                # may wake once on this causal boundary, never once per symbol.
                await process_broker_boundary(work)
            # The broker first consumes this completed boundary. V7 and BOS
            # then see its persisted 1s bar before activation/entry decisions.
            if observe_completed_seconds is not None:
                await observe_completed_seconds(work)
            if work.activation_rows and observe_activation is None:
                raise RuntimeError("Strategy 1 activation callback is required")
            for activation in work.activation_rows:
                await observe_activation(activation)
            for ticker, resolutions in work.broker_rows:
                if (ticker in candidate_rejections
                        and candidate_rejections[ticker] != 0
                        and ticker not in active):
                    continue
                # An active position still needs management, but a rejected
                # candidate must not authorize an entry/add in that callback.
                candidate = candidates.get(ticker)
                if candidate_rejections.get(ticker, 0):
                    candidate = None
                await evaluate_ticker(ticker, resolutions, candidate)
            await finish_boundary(work)
            desired = financially_active_tickers()
            if desired != scheduler.active_tickers:
                await asyncio.to_thread(scheduler.reconcile_financial_tickers,
                                        desired)
            count += 1
            if count % 256 == 0:
                await asyncio.sleep(0)
    finally:
        scheduler.close()
