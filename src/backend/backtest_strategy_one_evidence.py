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
from src.trading_runtime.strategy_one_position import ProtectionTransition


@dataclass(frozen=True, slots=True)
class StrategyOneEntryEvidence:
    candidate: StrategyOneDecisionCandidate
    activation: FrozenActivation
    bos: BosSnapshot
    bos_support: BosSupport | None
    protection: ProtectionTransition | None


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

    async def observe_completed_seconds(self, work: StrategyOneBoundaryWork) -> None:
        """Advance loaded V7/BOS books from the same certified market tape.

        Books first reached at this boundary still use their pinned catch-up
        read. Existing books consume each present 1s bar exactly once, without
        a redundant per-candidate ClickHouse request. Missing seconds are not
        fabricated; a later candidate performs the normal certified catch-up.
        """
        if not isinstance(work, StrategyOneBoundaryWork):
            raise TypeError("Strategy 1 V7 observation needs typed boundary work")
        rows = []
        for ticker, resolutions in work.broker_rows:
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
