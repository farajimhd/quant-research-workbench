"""Strategy 1 financial admission uses sealed facts, never a market builder."""
from dataclasses import replace
from datetime import date

import pytest

from src.backend.backtest_market_data import market_day_boundary
from src.backend.backtest_strategy_one_entry_product import ActivationFact, CandidateFact
from src.backend.backtest_strategy_one_market import StrategyOneDecisionCandidate
from src.backend.backtest_strategy_one_preparation import StrategyOneEntryCursor
from src.backend.backtest_strategy_one_stateful import propose_certified_strategy_one_entry
from src.trading_runtime.strategy_engine import AssignmentStatus, StrategyPermissions
from src.trading_runtime.strategy_one_stateful import StrategyOneFinancialView


def _facts():
    boundary = 31_000
    at = market_day_boundary(date(2026, 8, 18), boundary)
    quote_us = int(at.timestamp() * 1_000_000) - 100_000
    candidate = StrategyOneDecisionCandidate(
        {"session_date": "2026-08-18", "ticker": "AAA",
         "boundary_ms": boundary, "resolution_ms": 100,
         "indicator_resolution_ms": 100,
         "price_valid": 1, "quote_valid": 1,
         "quote_timestamp_us": quote_us,
         "bid_int": 100_000, "ask_int": 100_100},
        StrategyOneEntryCursor(boundary, "AAA", 0, 30_000,
                               (31_000, 30_000, 30_000, 30_000),
                               30_000, 99_000))
    fact = CandidateFact("AAA", boundary, 30_000, 30_000, "P1", 101_000,
                         "support", "R3", "P1", True, 9.89, 12., "R4", 3)
    activation = ActivationFact("AAA", 30_000, 100_000, .5, ("R1", "R2"))
    financial = StrategyOneFinancialView(
        "assignment-1", "DU1", "AAA", AssignmentStatus.WATCHING,
        StrategyPermissions(observe=True, enter=True), 0., False, False,
        False, 0)
    return candidate, fact, activation, financial


def test_completed_certified_entry_proposes_to_portfolio_without_order():
    result = propose_certified_strategy_one_entry(*_facts())
    assert result.reason == "entry_proposed"
    assert result.proposal is not None
    assert result.proposal.account_id == "DU1"
    assert result.proposal.assignment_id == "assignment-1"
    assert result.proposal.strategy_number == 1
    assert result.proposal.reference_ask == 10.01
    assert result.proposal.initial_stop == 9.89
    assert result.proposal.initial_target == 12.
    assert result.proposal.target_level_id == "R4"


@pytest.mark.parametrize("change,reason", [
    ({"position_quantity": 5.}, "position_requires_management"),
    ({"pending_entry": True}, "entry_fill_pending"),
    ({"pending_capital_request": True}, "entry_fill_pending"),
    ({"pending_exit": True}, "exit_fill_pending"),
    ({"status": AssignmentStatus.PAUSED}, "entry_permission_closed"),
    ({"completed_entries": 1}, "entry_permission_closed"),
    ({"reentry_not_before_ms": 31_100}, "reentry_cooldown"),
])
def test_financial_state_blocks_new_entry_without_losing_management(change, reason):
    candidate, fact, activation, financial = _facts()
    result = propose_certified_strategy_one_entry(
        candidate, fact, activation, replace(financial, **change))
    assert result.reason == reason
    assert result.proposal is None


def test_reentry_stays_closed_without_certified_prior_position_break():
    candidate, fact, activation, financial = _facts()
    permitted = replace(financial, completed_entries=1,
                        reentry_not_before_ms=31_000,
                        permissions=replace(financial.permissions, reenter=True))
    assert propose_certified_strategy_one_entry(
        candidate, fact, activation, permitted).reason == (
            "reentry_structure_confirmation_unavailable")


def test_missing_protection_and_stale_quote_fail_without_fabrication():
    candidate, fact, activation, financial = _facts()
    assert propose_certified_strategy_one_entry(
        candidate, replace(fact, protection_valid=False,
                           stop_price=None, target_price=None),
        activation, financial).reason == "initial_protection_unavailable"
    old = dict(candidate.market_row)
    old["quote_timestamp_us"] -= 1_000_001
    assert propose_certified_strategy_one_entry(
        replace(candidate, market_row=old), fact, activation,
        financial).reason == "fresh_quote_required"
    future = dict(candidate.market_row)
    future["quote_timestamp_us"] += 100_001
    with pytest.raises(ValueError, match="from the future"):
        propose_certified_strategy_one_entry(
            replace(candidate, market_row=future), fact, activation, financial)


def test_mismatched_episode_or_ticker_fails_before_financial_admission():
    candidate, fact, activation, financial = _facts()
    with pytest.raises(ValueError, match="differs"):
        propose_certified_strategy_one_entry(
            candidate, replace(fact, episode_start_ms=29_000),
            activation, financial)
    with pytest.raises(ValueError, match="differs"):
        propose_certified_strategy_one_entry(
            candidate, fact, replace(activation, ticker="BBB"), financial)
