from dataclasses import replace
from datetime import date
from math import nan

import pytest

from src.trading_runtime.domain import InstrumentContract
from src.trading_runtime.strategy_orders import IbkrStrategyOrderPlanner
from src.trading_runtime.strategy_forty_four_rules import (
    EntryFacts, ResistanceFact, SOURCE_CANDIDATE_ID, adaptive_stop,
    entry_geometry, entry_intents, incoming_score, protective_fee_reserve,
    propose_batch, selected_candidate, verify_selection,
)


def facts(**changes):
    return replace(EntryFacts(
        ticker="TEST", boundary_ms=20_000, admission_ms=20_000,
        session_end_ms=19_800_000, observed=True, quote_valid=True,
        quote_age_us=0, bid=9.98, ask=10., close=10., low=9.9, high=10.1,
        dollar_volume=10_000., trades=20, volume=1000., vwap=9.9,
        previous_five_second_close=9.9, previous_ten_second_mean_notional=5000.,
        swing_low=9.8, swing_available_ms=19_000, structural_boundary_ms=20_000,
        resistances=tuple(ResistanceFact(f"r{i}", 10.2 + i * .2) for i in range(15)),
        source_token="certified-source-plan", tradable=True), **changes)


def test_selection_matches_exact_reconciled_research_identity():
    from research.vectorized_backtest.v2.torch_backtest.grid import Candidate
    verify_selection()
    assert Candidate(**selected_candidate()).identity == SOURCE_CANDIDATE_ID


@pytest.mark.parametrize("changes", [
    {"tradable": False}, {"ticker": "LGHL"}, {"observed": False},
    {"quote_valid": False}, {"quote_age_us": 1_000_001},
    {"dollar_volume": 999.}, {"trades": 4}, {"bid": 9.89},
    {"swing_low": nan}, {"swing_low": 10.}, {"close": nan},
    {"admission_ms": 19_000}, {"structural_boundary_ms": 19_000},
    {"resistances": ()}, {"session_end_ms": 30_000},
])
def test_missing_stale_locked_or_failed_signal_is_not_deferred(changes):
    assert entry_geometry(facts(**changes), already_submitted=False) is None
    assert entry_geometry(facts(), already_submitted=True) is None


def test_immediate_signal_does_not_require_vwap_or_macd_open():
    assert entry_geometry(facts(vwap=nan, close=9.95), already_submitted=False)


@pytest.mark.parametrize("changes", [
    {"boundary_ms": 20_100}, {"swing_available_ms": 20_000},
    {"structural_boundary_ms": 21_000}, {"quote_age_us": -1},
    {"tradable": 1}, {"source_token": ""},
])
def test_future_or_malformed_sources_fail_closed(changes):
    with pytest.raises(ValueError):
        entry_geometry(facts(**changes), already_submitted=False)


def test_targets_frozen_nearest_fifteen_no_geometry_price_dedup():
    f = facts(resistances=(ResistanceFact("same-a", 10.2), ResistanceFact("same-b", 10.2))
              + tuple(ResistanceFact(f"x{i}", 10.4 + i * .2) for i in range(14)))
    batch = propose_batch(f, account_id="A", assignment_id="X",
                          free_cash_after_reservations=10_000., already_submitted=False)
    assert len(batch.legs) == 15
    assert batch.legs[0].target_level_id == "same-a"
    assert batch.legs[1].target_level_id == "same-b"
    assert batch.legs[0].target_price == batch.legs[1].target_price == 10.19
    assert batch.legs[-1].target_level_id == "x12"


def test_sizing_decreases_and_preserves_entry_and_exit_fee_cash():
    batch = propose_batch(facts(), account_id="A", assignment_id="X",
                          free_cash_after_reservations=10_000., already_submitted=False)
    quantities = [r.quantity for r in batch.legs]
    assert quantities == sorted(quantities, reverse=True)
    assert sum(r.quantity * (r.limit_price + .010) + 5. for r in batch.legs) <= 10_000.
    assert propose_batch(facts(), account_id="A", assignment_id="X",
                         free_cash_after_reservations=80., already_submitted=False) is None


def test_native_intent_and_order_contracts_have_fifteen_separate_single_slice_parents():
    batch = propose_batch(facts(), account_id="A", assignment_id="X",
                          free_cash_after_reservations=10_000., already_submitted=False)
    intents = entry_intents(batch, session_date=date(2026, 9, 3))
    assert intents == entry_intents(batch, session_date=date(2026, 9, 3))
    assert len({r.intent_id for r in intents}) == 15
    instrument = InstrumentContract(instrument_id="test:1", conid=1, symbol="TEST",
                                    security_type="STK", currency="USD", exchange="SMART")
    planner = IbkrStrategyOrderPlanner()
    parents = []
    for intent, leg in zip(intents, batch.legs):
        assert intent.metadata == {}
        assert len(intent.protection_profile.slices) == 1
        assert intent.execution_policy.envelope.deadline_ms == batch.facts.session_end_ms - 300_000 - batch.facts.boundary_ms
        assert not intent.capital_request.allow_replacement
        plan = planner.plan(account_id="A", instrument=instrument, intent=intent,
                            strategy_id="squeeze-grid-strategy", strategy_revision=44)
        buys = [r for r in plan.orders if r.side == "BUY"]
        sells = [r for r in plan.orders if r.side == "SELL"]
        assert len(buys) == 1 and len(sells) == 2
        assert int(buys[0].quantity) == leg.quantity
        assert sorted(r.orderType for r in sells) == ["LMT", "STP"]
        parents.append(buys[0].cOID)
    assert len(set(parents)) == 15


def test_trailing_uses_actual_100ms_first_fill_and_never_lowers_stop():
    params = dict(boundary_ms=20_000, first_fill_ms=10_100, current_stop=9.7,
                  average_entry=10., peak_after_entry=10.5, bid=10.4,
                  quote_valid=True, quote_age_us=0, completed_ten_second_mean_movement=.02)
    assert adaptive_stop(**params) == 9.7  # actual elapsed time is only 9.9s
    assert adaptive_stop(**{**params, "boundary_ms": 21_000}) == 10.39
    assert adaptive_stop(**{**params, "boundary_ms": 21_000,
                            "completed_ten_second_mean_movement": None}) == 9.7
    assert adaptive_stop(**{**params, "boundary_ms": 21_000, "bid": 9.6}) == 9.7


def test_scoring_missing_history_matches_research_zero_contribution():
    f = facts(vwap=nan, previous_five_second_close=nan,
              previous_ten_second_mean_notional=nan)
    geometry = entry_geometry(f, already_submitted=False)
    expected = .15 * .0998 + .15 * .19 / (.19 + .21)
    assert incoming_score(f, geometry) == pytest.approx(expected)


def test_protective_fee_reserve_covers_tiny_orders_and_four_exit_roles():
    assert protective_fee_reserve(held_and_pending_shares=1,
                                 exit_fees_paid_by_role=(0., 0., 0., 0.)) == 4.005
    assert protective_fee_reserve(held_and_pending_shares=100,
                                 exit_fees_paid_by_role=(1., .5, 0., 2.)) == 2.
    assert protective_fee_reserve(held_and_pending_shares=0,
                                 exit_fees_paid_by_role=(0., 0., 0., 0.)) == 0.
