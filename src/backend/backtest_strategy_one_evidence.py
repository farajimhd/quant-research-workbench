"""Causal evidence join for unpublished Strategy 1 entry decisions.

STRATEGY CREATION RULES: this read-only coordinator consumes pinned ARTE
products. It does not create bars, indicators, pivots, or market structure;
it does not authorize an order. Stateful financial decisions remain with the
numbered Strategy 1 evaluator and shared OMS.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date
from math import isfinite
from typing import Any, Mapping

from src.backend.backtest_market_data import (
    CertifiedMarketDayPlan, market_day_boundary,
)
from src.backend.backtest_strategy_one_activation import StrategyOneActivation
from src.backend.backtest_strategy_one_bos import (
    BosSnapshot, StrategyOneBosCursor,
)
from src.backend.backtest_strategy_one_decision import candidate_entry_protection
from src.backend.backtest_strategy_one_hod_store import CertifiedHodPlan
from src.backend.backtest_strategy_one_market import StrategyOneDecisionCandidate
from src.backend.backtest_strategy_one_scheduler import StrategyOneBoundaryWork
from src.backend.backtest_strategy_one_pivot_store import CertifiedPivotPlan
from src.backend.fixed_v7_stream import FixedV7Cache
from src.backend.structural_v7_seed import CertifiedSeedPlan
from src.trading_runtime.strategy_one_activation_state import (
    ActivationCatalog, FrozenActivation, freeze_strategy_one_activation,
)
from src.trading_runtime.strategy_one_bos import (
    BosSupport, supported_completed_bos,
)
from src.trading_runtime.strategy_one_position import (
    ProtectionTransition, ResistanceBreak,
)
from src.trading_runtime.strategy_one_resistance import (
    ResistanceObservation, observe_completed_resistance_second,
)


@dataclass(frozen=True, slots=True)
class StrategyOneEntryEvidence:
    candidate: StrategyOneDecisionCandidate
    activation: FrozenActivation
    bos: BosSnapshot
    bos_support: BosSupport | None
    protection: ProtectionTransition | None


@dataclass(frozen=True, slots=True)
class StrategyOneManagementEvidence:
    ticker: str
    boundary_ms: int
    bid: float | None
    ask: float | None
    price_bearing_bar: bool
    low_boundary_ms: int | None
    low_int: int | None
    breaks: tuple[ResistanceBreak, ...]
    overhead_levels: tuple[Mapping, ...]


class StrategyOneCausalEvidence:
    """One sequential, read-only V7/BOS lane per candidate ticker."""

    def __init__(self, *, market_plan: CertifiedMarketDayPlan,
                 seed_plan: CertifiedSeedPlan, pivot_plan: CertifiedPivotPlan,
                 hod_plan: CertifiedHodPlan,
                 session: date, client: Any) -> None:
        if (not isinstance(market_plan, CertifiedMarketDayPlan)
                or not isinstance(seed_plan, CertifiedSeedPlan)
                or not isinstance(pivot_plan, CertifiedPivotPlan)
                or not isinstance(hod_plan, CertifiedHodPlan)
                or pivot_plan.source_build_id != market_plan.build_id
                or hod_plan.source_build_id != market_plan.build_id
                or hod_plan.session_date != session.isoformat()
                or not {ticker for ticker, _ in hod_plan.contexts}
                <= set(market_plan.tickers)
                or pivot_plan.session_date != session.isoformat()
                or market_plan.sessions != (session.isoformat(),)
                or not set(ticker for ticker, _ in pivot_plan.intervals)
                <= set(market_plan.tickers)):
            raise ValueError("Strategy 1 causal evidence plans disagree")
        self.session = session
        self.hod = hod_plan
        self.bos = StrategyOneBosCursor(pivot_plan)
        self.v7 = FixedV7Cache(
            market_plan=market_plan, seed_plan=seed_plan,
            session=session, client=client,
            observe_completed_second=self.bos.observe_second,
            prefetch_horizon_ms=300_000)
        self.activations = ActivationCatalog()
        self._resistance: dict[str, ResistanceObservation] = {}
        self._completed_breaks: dict[str, tuple[ResistanceBreak, ...]] = {}
        self._break_boundary_ms = 0
        self._completed_30s: dict[str, Mapping[str, Any]] = {}

    async def observe_completed_seconds(self, work: StrategyOneBoundaryWork) -> None:
        """Advance loaded V7/BOS books from the same certified market tape.

        Books first reached at this boundary still use their pinned catch-up
        read. Existing books consume each present 1s bar exactly once, without
        a redundant per-candidate ClickHouse request. Missing seconds are not
        fabricated; a later candidate performs the normal certified catch-up.
        """
        if not isinstance(work, StrategyOneBoundaryWork):
            raise TypeError("Strategy 1 V7 observation needs typed boundary work")
        if work.boundary_ms <= self._break_boundary_ms:
            raise ValueError("Strategy 1 resistance clock did not advance")
        self._break_boundary_ms = work.boundary_ms
        self._completed_breaks = {}
        rows = []
        for ticker, resolutions in work.broker_rows:
            low_row = resolutions.get(30_000)
            if low_row is not None:
                if (low_row.get("session_date") != self.session.isoformat()
                        or low_row.get("ticker") != ticker
                        or low_row.get("resolution_ms") != 30_000
                        or low_row.get("boundary_ms") != work.boundary_ms):
                    raise ValueError("Strategy 1 completed 30s low differs from boundary")
                self._completed_30s[ticker] = low_row
            row = resolutions.get(1_000)
            if row is None or not self.v7.has_stream(ticker):
                continue
            if (row.get("session_date") != self.session.isoformat()
                    or row.get("ticker") != ticker
                    or row.get("resolution_ms") != 1_000
                    or row.get("boundary_ms") != work.boundary_ms):
                raise ValueError("Strategy 1 V7 second differs from boundary")
            rows.append(row)
        if rows:
            at = market_day_boundary(self.session, work.boundary_ms)
            if self.v7.prefetches_seconds:
                await asyncio.to_thread(
                    self.v7.catch_up_seconds,
                    tuple(str(row["ticker"]) for row in rows), at=at)
            else:
                await asyncio.to_thread(self.v7.advance_seconds, rows, at=at)
            # Acceptance uses the just-completed 1s close against geometry
            # known before that second. No intrabucket trade ordering exists.
            for row in rows:
                ticker = str(row["ticker"])
                levels = self.v7.strategy_one_levels(ticker, as_of=at)
                state, breaks = observe_completed_resistance_second(
                    self._resistance.get(ticker, ResistanceObservation()),
                    row, admitted_levels=levels)
                self._resistance[ticker] = state
                self._completed_breaks[ticker] = breaks

    def completed_resistance_breaks(
        self, ticker: str, *, boundary_ms: int,
    ) -> tuple[ResistanceBreak, ...]:
        """Expose only witnesses from the exact completed global boundary."""
        if (not ticker or type(boundary_ms) is not int
                or boundary_ms != self._break_boundary_ms):
            raise ValueError("Strategy 1 break request differs from completed clock")
        return self._completed_breaks.get(ticker, ())

    def completed_30s_low(
        self, ticker: str, *, boundary_ms: int,
    ) -> Mapping[str, Any] | None:
        """Return only the last valid, still-current completed 30s source row."""
        if (not ticker or type(boundary_ms) is not int
                or boundary_ms != self._break_boundary_ms):
            raise ValueError("Strategy 1 30s low request differs from completed clock")
        row = self._completed_30s.get(ticker)
        if (row is None or row.get("price_valid") != 1
                or row.get("extremes_valid") != 1
                or type(row.get("low_int")) is not int
                or row["low_int"] <= 0
                or not 0 <= boundary_ms - row["boundary_ms"] < 30_000):
            return None
        return row

    async def management_evidence(
        self, ticker: str, resolutions: Mapping[int, Mapping], *,
        boundary_ms: int,
    ) -> StrategyOneManagementEvidence:
        """Join only completed persisted bars and a fresh liquidity quote."""
        if (not ticker or not isinstance(resolutions, Mapping)
                or type(boundary_ms) is not int
                or boundary_ms != self._break_boundary_ms):
            raise ValueError("Strategy 1 management differs from market clock")
        breaks = self.completed_resistance_breaks(ticker, boundary_ms=boundary_ms)
        low = self.completed_30s_low(ticker, boundary_ms=boundary_ms)
        row = resolutions.get(100)
        bid = ask = None
        price_bearing = False
        levels: tuple[Mapping, ...] = ()
        if row is not None:
            if (not isinstance(row, Mapping)
                    or row.get("session_date") != self.session.isoformat()
                    or row.get("ticker") != ticker
                    or row.get("resolution_ms") != 100
                    or row.get("boundary_ms") != boundary_ms):
                raise ValueError("Strategy 1 liquidity row differs from management")
            price_bearing = row.get("price_valid") == 1
            if row.get("quote_valid") == 1:
                bid_int, ask_int = row.get("bid_int"), row.get("ask_int")
                quote_at = row.get("quote_timestamp_us")
                if (any(type(value) is not int for value in (
                        bid_int, ask_int, quote_at))
                        or not 0 < bid_int <= ask_int):
                    raise ValueError("Strategy 1 management quote is malformed")
                now_us = round(market_day_boundary(
                    self.session, boundary_ms).timestamp() * 1_000_000)
                if 0 <= now_us - quote_at <= 1_000_000:
                    bid, ask = bid_int / 10_000, ask_int / 10_000
                    # The ordinal target may rerank only after a completed
                    # price-bearing bar. Quote-only buckets still carry the
                    # executable quote and last completed 30s swing low, but
                    # must not load or use V7 overhead geometry.
                    if price_bearing:
                        levels = await self._levels(ticker, boundary_ms)
        return StrategyOneManagementEvidence(
            ticker, boundary_ms, bid, ask, price_bearing,
            int(low["boundary_ms"]) if low is not None else None,
            int(low["low_int"]) if low is not None else None,
            breaks, levels)

    async def _levels(self, ticker: str, boundary_ms: int) -> tuple[Mapping, ...]:
        at = market_day_boundary(self.session, boundary_ms)
        if self.v7.strategy_one_ready_without_read(ticker, as_of=at):
            return self.v7.strategy_one_levels(ticker, as_of=at)
        return await asyncio.to_thread(
            self.v7.strategy_one_levels, ticker, as_of=at)

    async def observe_activation(self, activation: StrategyOneActivation) -> FrozenActivation:
        if not isinstance(activation, StrategyOneActivation):
            raise ValueError("Strategy 1 activation is not certified")
        levels = await self._levels(activation.ticker, activation.boundary_ms)
        prior = self.v7.last_completed_price_second(activation.ticker)
        if prior is not None and activation.ticker not in self._resistance:
            state, breaks = observe_completed_resistance_second(
                ResistanceObservation(), prior, admitted_levels=levels)
            if breaks:
                raise RuntimeError("Strategy 1 first resistance observation broke a level")
            self._resistance[activation.ticker] = state
        frozen = freeze_strategy_one_activation(
            session_date=self.session.isoformat(), ticker=activation.ticker,
            boundary_ms=activation.boundary_ms, price_int=activation.price_int,
            admitted_levels=levels)
        self.activations.add(frozen)
        return frozen

    async def entry_evidence(self, candidate: StrategyOneDecisionCandidate, *,
                             tick: float) -> StrategyOneEntryEvidence:
        if (not isinstance(candidate, StrategyOneDecisionCandidate)
                or type(tick) not in (int, float) or not isfinite(tick)
                or tick <= 0):
            raise ValueError("Strategy 1 candidate evidence is not typed")
        row, cursor = candidate.market_row, candidate.evidence
        if (row.get("session_date") != self.session.isoformat()
                or row.get("ticker") != cursor.ticker
                or row.get("boundary_ms") != cursor.boundary_ms
                or cursor.episode_start_ms > cursor.boundary_ms):
            raise ValueError("Strategy 1 candidate differs from pinned session")
        activation = self.activations.get(cursor.ticker, cursor.episode_start_ms)
        levels = await self._levels(cursor.ticker, cursor.boundary_ms)
        bos = self.bos.snapshot(cursor.ticker, boundary_ms=cursor.boundary_ms)
        support = supported_completed_bos(
            bos.open_break, candidate_boundary_ms=cursor.boundary_ms,
            visible_pivots=bos.visible_pivots, admitted_levels=levels)
        protection = candidate_entry_protection(
            candidate, admitted_v7_levels=levels,
            hod_context=self.hod.lookup(cursor.ticker, cursor.boundary_ms),
            tick=tick)
        return StrategyOneEntryEvidence(
            candidate, activation, bos, support, protection)
