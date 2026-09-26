"""Read-only adapter from sealed Strategy 1 entry facts to financial admission.

The same pure numbered reducer can consume equivalent completed QMD/live
facts. This adapter never derives an indicator or submits an order.
"""
from __future__ import annotations

from datetime import date

from src.backend.backtest_market_data import market_day_boundary
from src.backend.backtest_strategy_one_entry_product import ActivationFact, CandidateFact
from src.backend.backtest_strategy_one_market import StrategyOneDecisionCandidate
from src.trading_runtime.strategy_one_stateful import (
    StrategyOneEntryDecision, StrategyOneEntryInput,
    StrategyOneFinancialView, propose_strategy_one_entry,
)


def propose_certified_strategy_one_entry(
    candidate: StrategyOneDecisionCandidate, fact: CandidateFact,
    activation: ActivationFact, financial: StrategyOneFinancialView,
) -> StrategyOneEntryDecision:
    """Use only the certified completed row and exact producer-owned scalars."""
    if (not isinstance(candidate, StrategyOneDecisionCandidate)
            or not isinstance(fact, CandidateFact)
            or not isinstance(activation, ActivationFact)):
        raise TypeError("Strategy 1 stateful adapter needs typed certified facts")
    row, cursor = candidate.market_row, candidate.evidence
    if (row.get("ticker") != cursor.ticker or row.get("ticker") != fact.ticker
            or row.get("boundary_ms") != cursor.boundary_ms
            or row.get("boundary_ms") != fact.boundary_ms
            or cursor.episode_start_ms != fact.episode_start_ms
            or activation.ticker != fact.ticker
            or activation.episode_start_ms != fact.episode_start_ms
            or row.get("resolution_ms") != 100
            or row.get("price_valid") != 1
            or row.get("quote_valid") != 1
            or not isinstance(row.get("session_date"), str)):
        raise ValueError("Strategy 1 financial input differs from sealed candidate")
    quote_at = row.get("quote_timestamp_us")
    bid_int, ask_int = row.get("bid_int"), row.get("ask_int")
    if (type(quote_at) is not int or type(bid_int) is not int
            or type(ask_int) is not int):
        raise ValueError("Strategy 1 candidate quote lacks integer source fields")
    now_us = int(market_day_boundary(
        date.fromisoformat(row["session_date"]), fact.boundary_ms
    ).timestamp() * 1_000_000)
    if quote_at > now_us:
        raise ValueError("Strategy 1 candidate quote is from the future")
    evidence = StrategyOneEntryInput(
        fact.ticker, fact.boundary_ms, fact.episode_start_ms,
        activation.average_gap, fact.bos_break_boundary_ms,
        fact.bos_support_level_id, fact.protection_valid,
        fact.stop_price, fact.target_price, fact.target_level_id,
        fact.target_ordinal, bid_int, ask_int, now_us - quote_at)
    return propose_strategy_one_entry(evidence, financial)
