"""Prepared scalar persistence/state checks; no connected native publication."""
from dataclasses import replace
from datetime import date

import pytest

from src.backend.backtest_strategy_one_management import StrategyOneManagementState
from src.trading_runtime.arte_liquidity_fade_failure_v4 import (
    LIQUIDITY_FADE_FAILURE, project_liquidity_fade_failure, restore_liquidity_fade_failure,
)
from src.trading_runtime.strategy_liquidity_fade_exit import liquidity_fade_exit_intent
from src.trading_runtime.strategy_liquidity_fade_failure import liquidity_fade_failure
from src.trading_runtime.strategy_liquidity_fade_source import validate_liquidity_fade_state
from src.trading_runtime.strategy_one_stateful import StrategyOneFinancialView, StrategyOneEntryProposal
from src.trading_runtime.strategy_one_position import ProtectionState
from src.trading_runtime.strategy_engine import AssignmentStatus, StrategyPermissions
from test_strategy_liquidity_fade_failure import observed_case

IDENTITY = "325dcc8d-1171-52c5-b944-a92ac772d865"


def prepared_case():
    w = liquidity_fade_failure(observed_case())
    held = StrategyOneFinancialView("assignment", "account", "PLUG", AssignmentStatus.MANAGING,
                                   StrategyPermissions(), 100.0, False, False, False, 1)
    args = dict(session_date=date(2026, 8, 10), source_entry_intent_id=IDENTITY)
    intent = liquidity_fade_exit_intent(w, held, **args)
    row = project_liquidity_fade_failure(w, intent, held, **args, run_id="run",
        batch_id=IDENTITY, parent_record_id=IDENTITY, source_build_id="a"*64,
        source_bars_attempt_id=IDENTITY, source_indicators_attempt_id=IDENTITY,
        source_liquidity_attempt_id=IDENTITY, source_market_plan_token="b"*64,
        source_manager_snapshot_id=IDENTITY, source_manager_checkpoint_sequence=64,
        source_manager_snapshot_hash="c"*64, source_broker_snapshot_id=IDENTITY,
        source_broker_snapshot_hash="d"*64)
    key = held.account_id, held.assignment_id, held.ticker
    proposal = StrategyOneEntryProposal(held.assignment_id, held.account_id, held.ticker,
        44_780_000, 44_770_000, 2.33, 2.30, 2.40, "R1", .07, 44_779_000, "S1", 35)
    state = StrategyOneManagementState(w.boundary_ms, ((key, proposal),),
        ((key, ProtectionState(w.boundary_ms, 2.30, 2.40)),), (), ((key, 23300),), (),
        ((key, w.first_held_boundary_ms),))
    return w, held, state, row


def test_complete_scalar_roundtrip_preserves_both_clocks_four_counts_and_source():
    w, held, state, row = prepared_case()
    assert restore_liquidity_fade_failure(row) == w
    assert set(row) == {name for name, _ in LIQUIDITY_FADE_FAILURE.columns} - {"content_hash"}
    assert row["boundary_ms"] != row["completed_five_second_boundary_ms"]
    assert tuple(row[f"trade_count_{i}"] for i in range(4)) == (57, 18, 8, 5)
    assert validate_liquidity_fade_state(w, state, held) == state.submitted[0][1]


@pytest.mark.parametrize("field,value", [
    ("strategy_number", 34), ("strategy_number", True), ("trade_count_0", True),
    ("trade_count_0", 1), ("trade_count_3", -1), ("quote_age_us", 1_000_001),
    ("completed_five_second_boundary_ms", 44_810_000), ("first_held_boundary_ms", 44_785_100),
    ("macd_line", float("nan")), ("source_market_plan_token", "bad"),
    ("source_indicators_attempt_id", "bad"), ("source_liquidity_attempt_id", "bad"),
    ("source_manager_snapshot_id", "bad"),
    ("source_manager_snapshot_id", "00000000-0000-0000-0000-000000000000"),
    ("source_manager_snapshot_hash", "bad"), ("source_manager_checkpoint_sequence", True),
    ("source_manager_checkpoint_sequence", 0), ("source_manager_checkpoint_sequence", 2**64),
    ("source_broker_snapshot_id", "bad"),
    ("source_broker_snapshot_id", "00000000-0000-0000-0000-000000000000"),
    ("source_broker_snapshot_hash", "bad"),
])
def test_changed_missing_or_malformed_scalar_authority_rejects(field, value):
    *_, row = prepared_case()
    with pytest.raises(ValueError):
        restore_liquidity_fade_failure(dict(row, **{field: value}))
    del row[field]
    with pytest.raises(ValueError):
        restore_liquidity_fade_failure(row)


@pytest.mark.parametrize("family", ["submitted", "positions", "first_held_boundaries"])
def test_missing_duplicate_and_foreign_original_state_rejects(family):
    w, held, state, _ = prepared_case()
    for rows in ((), getattr(state, family)*2):
        with pytest.raises(ValueError):
            validate_liquidity_fade_state(w, replace(state, **{family: rows}), held)
    with pytest.raises(ValueError):
        validate_liquidity_fade_state(w, state, replace(held, account_id="OTHER"))


def test_valid_scalar_boundary_still_requires_exact_native_first_held_state():
    w, held, state, row = prepared_case()
    # Equality at the oldest interval's start is semantically whole-held. A
    # scalar mapper cannot certify its origin; the independently verified
    # native state must reject a different but plausible first-held time.
    altered = restore_liquidity_fade_failure(dict(row, first_held_boundary_ms=44_785_000))
    assert altered.first_held_boundary_ms == 44_785_000
    with pytest.raises(ValueError, match="first-held authority"):
        validate_liquidity_fade_state(altered, state, held)


@pytest.mark.parametrize("quantity", [float("nan"), float("inf"), True, -1, 0])
def test_state_gate_rejects_invalid_financial_quantity(quantity):
    w, held, state, _ = prepared_case()
    with pytest.raises(ValueError, match="financial authority"):
        validate_liquidity_fade_state(w, state, replace(held, position_quantity=quantity))


@pytest.mark.parametrize("field,value", [("strategy_number", 34), ("reference_ask", 2.34),
                                        ("initial_stop", 2.29), ("boundary_ms", 44_784_100)])
def test_a_parent_or_changed_original_risk_cannot_be_relabelled(field, value):
    w, held, state, _ = prepared_case()
    key, source = state.submitted[0]
    with pytest.raises(ValueError):
        validate_liquidity_fade_state(w, replace(state, submitted=((key, replace(source, **{field: value})),)), held)


def test_prepared_candidate_has_no_installed_runtime_admission():
    from src.trading_runtime.numbered_fixed_strategy import numbered_fixed_strategy
    with pytest.raises(ValueError):
        numbered_fixed_strategy(35)
