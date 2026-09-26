"""Causal financial admission for unpublished, immutable Strategy 1.

STRATEGY CREATION RULES: vectorized producer-certified rule evidence arrives
before this reducer. It may not derive bars, MACD, liquidity, or V7 geometry.
It never submits an order or mutates shared cash. Portfolio/OMS alone decide
whether a proposed entry is funded and executable. After publication, any
behavior change requires a new Strategy number, not an edit to this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from .strategy_engine import AssignmentStatus, StrategyPermissions
from .strategy_one_contract import STRATEGY_NUMBER


@dataclass(frozen=True, slots=True)
class StrategyOneFinancialView:
    assignment_id: str
    account_id: str
    ticker: str
    status: AssignmentStatus
    permissions: StrategyPermissions
    position_quantity: float
    pending_entry: bool
    pending_exit: bool
    pending_capital_request: bool
    completed_entries: int
    reentry_not_before_ms: int = 0


@dataclass(frozen=True, slots=True)
class StrategyOneEntryInput:
    ticker: str
    boundary_ms: int
    episode_start_ms: int
    activation_gap: float | None
    bos_break_boundary_ms: int | None
    bos_support_level_id: str
    protection_valid: bool
    stop_price: float | None
    target_price: float | None
    target_level_id: str
    target_ordinal: int | None
    bid_int: int
    ask_int: int
    quote_age_us: int


@dataclass(frozen=True, slots=True)
class StrategyOneEntryProposal:
    assignment_id: str
    account_id: str
    ticker: str
    boundary_ms: int
    episode_start_ms: int
    reference_ask: float
    initial_stop: float
    initial_target: float
    target_level_id: str
    frozen_gap: float
    bos_break_boundary_ms: int
    bos_support_level_id: str
    strategy_number: int = STRATEGY_NUMBER


@dataclass(frozen=True, slots=True)
class StrategyOneEntryDecision:
    reason: str
    proposal: StrategyOneEntryProposal | None = None


def propose_strategy_one_entry(
    evidence: StrategyOneEntryInput, financial: StrategyOneFinancialView,
) -> StrategyOneEntryDecision:
    """Admit only a flat, permitted, current episode to shared Portfolio/OMS.

    Inputs are completed-boundary facts. A proposal is not a reservation,
    command, fill, or permission to skip the broker's activation delay.
    """
    if (not isinstance(evidence, StrategyOneEntryInput)
            or not isinstance(financial, StrategyOneFinancialView)
            or not evidence.ticker or evidence.ticker != evidence.ticker.upper()
            or evidence.ticker != financial.ticker
            or not financial.assignment_id or not financial.account_id
            or not isinstance(financial.status, AssignmentStatus)
            or not isinstance(financial.permissions, StrategyPermissions)
            or type(evidence.boundary_ms) is not int
            or not 0 < evidence.boundary_ms <= 57_600_000
            or evidence.boundary_ms % 100
            or type(evidence.episode_start_ms) is not int
            or not 0 < evidence.episode_start_ms <= evidence.boundary_ms
            or evidence.episode_start_ms % 100
            or type(financial.completed_entries) is not int
            or financial.completed_entries < 0
            or any(type(value) is not bool for value in (
                financial.pending_entry, financial.pending_exit,
                financial.pending_capital_request))
            or type(financial.reentry_not_before_ms) is not int
            or financial.reentry_not_before_ms < 0
            or type(financial.position_quantity) not in (int, float)
            or not isfinite(financial.position_quantity)
            or financial.position_quantity < 0):
        raise ValueError("Strategy 1 financial admission has invalid causal identity")
    if evidence.boundary_ms - evidence.episode_start_ms > 300_000:
        return StrategyOneEntryDecision("squeeze_episode_expired")
    if financial.position_quantity > 0:
        return StrategyOneEntryDecision("position_requires_management")
    if financial.pending_exit:
        return StrategyOneEntryDecision("exit_fill_pending")
    if financial.pending_entry or financial.pending_capital_request \
            or financial.status == AssignmentStatus.ENTRY_PENDING:
        return StrategyOneEntryDecision("entry_fill_pending")
    if financial.status in {
            AssignmentStatus.DISABLED, AssignmentStatus.PAUSED,
            AssignmentStatus.COMPLETED, AssignmentStatus.ERROR,
    } or not financial.permissions.observe or not (
            financial.permissions.reenter if financial.completed_entries
            else financial.permissions.enter):
        return StrategyOneEntryDecision("entry_permission_closed")
    if evidence.boundary_ms < financial.reentry_not_before_ms:
        return StrategyOneEntryDecision("reentry_cooldown")
    if (evidence.activation_gap is None
            or type(evidence.activation_gap) is not float
            or not isfinite(evidence.activation_gap)
            or evidence.activation_gap <= 0):
        return StrategyOneEntryDecision("activation_resistance_gap_unavailable")
    if (type(evidence.bos_break_boundary_ms) is not int
            or not 0 < evidence.bos_break_boundary_ms <= evidence.boundary_ms
            or evidence.bos_break_boundary_ms % 1_000):
        return StrategyOneEntryDecision("waiting_for_confirmed_swing_high_bos")
    if not evidence.bos_support_level_id:
        return StrategyOneEntryDecision("supported_local_structure_bos_required")
    if (not evidence.protection_valid
            or type(evidence.protection_valid) is not bool
            or type(evidence.stop_price) is not float
            or type(evidence.target_price) is not float
            or not evidence.target_level_id
            or evidence.target_ordinal != 3
            or not all(isfinite(value) for value in (
                evidence.stop_price, evidence.target_price))):
        return StrategyOneEntryDecision("initial_protection_unavailable")
    if (type(evidence.bid_int) is not int
            or type(evidence.ask_int) is not int
            or type(evidence.quote_age_us) is not int
            or not 0 < evidence.bid_int <= evidence.ask_int
            or not 0 <= evidence.quote_age_us <= 1_000_000):
        return StrategyOneEntryDecision("fresh_quote_required")
    bid, ask = evidence.bid_int / 10_000, evidence.ask_int / 10_000
    if not 0 < evidence.stop_price < bid <= ask < evidence.target_price:
        return StrategyOneEntryDecision("unrepresentable_stop_or_target")
    return StrategyOneEntryDecision(
        "entry_proposed",
        StrategyOneEntryProposal(
            financial.assignment_id, financial.account_id, evidence.ticker,
            evidence.boundary_ms,
            evidence.episode_start_ms, ask, evidence.stop_price,
            evidence.target_price, evidence.target_level_id,
            evidence.activation_gap, evidence.bos_break_boundary_ms,
            evidence.bos_support_level_id),
    )
