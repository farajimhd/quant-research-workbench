"""Causal, metadata-free add rule for the unpublished Strategy 1.

STRATEGY CREATION RULES: only completed, producer-owned bars, indicators and
V7 resistance witnesses may enter this reducer. It never computes indicators,
submits orders, changes Portfolio cash, or interprets aggregate bar interiors.
One completed 1s boundary and its co-terminating completed 100ms bar form the
add clock; partial executions do not consume additional purchase slots.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Mapping

from .strategy_one_contract import STRATEGY_NUMBER
from .strategy_one_position import ProtectionState, ResistanceBreak
from .strategy_one_stateful import StrategyOneFinancialView


@dataclass(frozen=True, slots=True)
class StrategyOneAddProposal:
    account_id: str
    assignment_id: str
    ticker: str
    boundary_ms: int
    resistance_id: str
    resistance_midpoint: float
    reference_ask: float
    working_stop: float
    working_target: float
    purchase_ordinal: int
    strategy_number: int = STRATEGY_NUMBER


def propose_strategy_one_add(
    financial: StrategyOneFinancialView, protection: ProtectionState,
    resistance: ResistanceBreak, resolutions: Mapping[int, Mapping], *,
    boundary_ms: int, purchase_ordinal: int,
    fresh_bid: float | None, fresh_ask: float | None,
    prior_accepted_ids: frozenset[str],
) -> StrategyOneAddProposal | None:
    """Admit a distinct break only on co-terminated closed-bar evidence.

    Both MACD samples must be persisted and bullish at their own completed
    boundaries. No forming value, prior close carried through an empty bar,
    or fabricated first trade can satisfy this rule.
    """
    if (not isinstance(financial, StrategyOneFinancialView)
            or not isinstance(protection, ProtectionState)
            or not isinstance(resistance, ResistanceBreak)
            or not isinstance(resolutions, Mapping)
            or type(boundary_ms) is not int or boundary_ms <= 0
            or boundary_ms % 1_000
            or resistance.completed_boundary_ms != boundary_ms
            or type(purchase_ordinal) is not int
            or purchase_ordinal not in (2, 3)
            or not isinstance(prior_accepted_ids, frozenset)):
        raise ValueError("Strategy 1 add lacks a completed distinct break clock")
    level = resistance.level
    identity = level.get("unified_level_id")
    lower, upper = level.get("lower"), level.get("upper")
    if (not isinstance(identity, str) or not identity
            or identity in prior_accepted_ids
            or identity not in protection.accepted_ids
            or level.get("role") != "resistance"
            or level.get("side") != "resistance"
            or type(lower) not in (int, float)
            or type(upper) not in (int, float)
            or not isfinite(lower) or not isfinite(upper)
            or not 0 < lower <= upper):
        raise ValueError("Strategy 1 add requires new pinned resistance geometry")
    if (financial.position_quantity <= 0 or financial.pending_exit
            or financial.pending_entry or financial.pending_capital_request
            or financial.current_purchase_groups != purchase_ordinal - 1):
        return None
    hundred, second = resolutions.get(100), resolutions.get(1_000)
    if hundred is None or second is None:
        return None
    for resolution, row in ((100, hundred), (1_000, second)):
        if (not isinstance(row, Mapping)
                or row.get("ticker") != financial.ticker
                or row.get("resolution_ms") != resolution
                or row.get("boundary_ms") != boundary_ms
                or row.get("price_valid") != 1
                or row.get("indicator_resolution_ms") != resolution):
            return None
        line, signal = row.get("macd_line"), row.get("macd_signal")
        if (type(line) not in (int, float)
                or type(signal) not in (int, float)
                or not isfinite(line) or not isfinite(signal)
                or line <= signal):
            return None
    opened, closed = second.get("open_int"), second.get("close_int")
    close_100 = hundred.get("close_int")
    if (any(type(value) is not int or value <= 0
            for value in (opened, closed, close_100))
            or closed <= opened):
        return None
    midpoint = (float(lower) + float(upper)) / 2
    if close_100 <= midpoint * 10_000:
        return None
    ask_int, bid_int = hundred.get("ask_int"), hundred.get("bid_int")
    if (hundred.get("quote_valid") != 1
            or type(ask_int) is not int or type(bid_int) is not int
            or not 0 < bid_int <= ask_int):
        return None
    ask, bid = ask_int / 10_000, bid_int / 10_000
    if (fresh_bid is None or fresh_ask is None
            or type(fresh_bid) not in (int, float)
            or type(fresh_ask) not in (int, float)
            or not isfinite(fresh_bid) or not isfinite(fresh_ask)
            or abs(bid - fresh_bid) > 1e-9
            or abs(ask - fresh_ask) > 1e-9
            or not 0 < protection.stop < bid <= ask < protection.target):
        return None
    return StrategyOneAddProposal(
        financial.account_id, financial.assignment_id, financial.ticker,
        boundary_ms, identity, midpoint, ask, protection.stop,
        protection.target, purchase_ordinal)
