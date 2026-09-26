"""Typed Strategy 1 entry projection from certified completed ARTE evidence.

STRATEGY CREATION RULES: this is unpublished Strategy 1 only. A future
behavior change gets a new strategy number; no strategy computes indicators,
bars, or market structure. This pure projection neither submits orders nor
writes journal/market data. Shared OMS authority applies its result.
"""
from __future__ import annotations

import asyncio
from datetime import date
from math import isfinite
from typing import Mapping, Sequence

from src.backend.backtest_market_data import market_day_boundary
from src.backend.backtest_strategy_one_market import StrategyOneDecisionCandidate
from src.backend.fixed_v7_stream import FixedV7Cache
from src.trading_runtime.strategy_one_position import (
    ProtectionTransition, open_protection,
)


def candidate_entry_protection(
    candidate: StrategyOneDecisionCandidate, *,
    admitted_v7_levels: Sequence[Mapping], tick: float,
) -> ProtectionTransition | None:
    """Use the certified closed 30s low and live-at-boundary persisted quote.

    V7 identity/policy admission belongs to the caller's certified seed plan.
    This function never substitutes a forming low, stale quote, or level list.
    """
    if (not isinstance(candidate, StrategyOneDecisionCandidate)
            or not isinstance(admitted_v7_levels, (tuple, list))
            or type(tick) not in (int, float) or not isfinite(tick)
            or tick <= 0):
        raise ValueError("Strategy 1 entry needs certified typed evidence")
    row, evidence = candidate.market_row, candidate.evidence
    boundary = row.get("boundary_ms")
    if (type(boundary) is not int or boundary != evidence.boundary_ms
            or row.get("ticker") != evidence.ticker
            or row.get("resolution_ms") != 100
            or row.get("price_valid") != 1
            or row.get("quote_valid") != 1):
        raise ValueError("Strategy 1 entry market identity differs from candidate")
    quote_at = row.get("quote_timestamp_us")
    bid_int, ask_int = row.get("bid_int"), row.get("ask_int")
    if any(type(value) is not int for value in (quote_at, bid_int, ask_int)):
        raise ValueError("Strategy 1 entry quote lacks integer source fields")
    now_us = int(market_day_boundary(
        date.fromisoformat(str(row["session_date"])), boundary
    ).timestamp() * 1_000_000)
    if (not 0 <= now_us - quote_at <= 1_000_000
            or not 0 < bid_int <= ask_int):
        return None
    return open_protection(
        now_ms=boundary, bid=bid_int / 10_000, ask=ask_int / 10_000,
        tick=tick, low_boundary_ms=evidence.stop_bar_boundary_ms,
        low_int=evidence.stop_low_int, low_price_valid=True,
        low_extremes_valid=True, overhead_levels=admitted_v7_levels)


async def certified_v7_candidate_protection(
    candidate: StrategyOneDecisionCandidate, *,
    v7_cache: FixedV7Cache, tick: float,
) -> ProtectionTransition | None:
    """Join one candidate to its pinned, causally caught-up V7 geometry."""
    if (not isinstance(candidate, StrategyOneDecisionCandidate)
            or not isinstance(v7_cache, FixedV7Cache)):
        raise TypeError("Strategy 1 needs certified candidate and V7 cache")
    row = candidate.market_row
    at = market_day_boundary(date.fromisoformat(
        str(row["session_date"])), candidate.evidence.boundary_ms)
    ticker = candidate.evidence.ticker
    if v7_cache.strategy_one_ready_without_read(ticker, as_of=at):
        levels = v7_cache.strategy_one_levels(ticker, as_of=at)
    else:
        levels = await asyncio.to_thread(
            v7_cache.strategy_one_levels, ticker, as_of=at)
    return candidate_entry_protection(
        candidate, admitted_v7_levels=levels, tick=tick)
