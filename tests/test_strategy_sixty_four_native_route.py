"""Exercise installed declaration selection through real native methods.

The reused fixtures replace external certified producer/catalog reads only;
these are runnable native component tests, not a financial session or approval.
"""
import pytest
from dataclasses import replace

from test_strategy_session_momentum_bootstrap import (
    test_real_session_bootstrap_reaches_exact_selected_momentum_parent as bootstrap,
)
from test_entry_spread_risk_recovery_native import (
    test_real_coordinator_rejects_cost_before_financial_lookup as rejection,
    test_native_runtime_intent_and_independent_cold_graph_reproduce_quote_cost as projection,
    test_actual_entry_page_and_manager_attachment as recovery,
)


def test_actual_async_bootstrap_keeps_parent_ten_percent_momentum(monkeypatch):
    bootstrap(monkeypatch, 64)


def test_actual_coordinator_cost_rejection_precedes_portfolio(monkeypatch):
    rejection(monkeypatch, 64)


def test_actual_native_intent_and_complete_companion_projection(monkeypatch):
    projection(monkeypatch, 64)


@pytest.mark.parametrize('mutation', [None, 'reference', 'stop', 'missing_stop', 'foreign_source', 'future_clock'])
def test_actual_committed_page_and_manager_source_recovery(monkeypatch, mutation):
    recovery(monkeypatch, 64, mutation)


def test_inherited_zero_regime_exit_roundtrips_without_parent_disguise():
    from test_strategy_forty_six_failure_route import fixture, witness
    from src.trading_runtime.strategy_followthrough_exit import validate_witness
    from src.trading_runtime.arte_followthrough_failure_v4 import seal_followthrough_rows, restore_failure
    from src.trading_runtime.arte_journal_writer import _sealed_families
    value = replace(witness(), completed_close_int=99_400, bid=9.94, ask=9.95)
    validate_witness(value, strategy_number=42)
    intent, base, row, source, source_event, entry = fixture(value, number=64)
    families = dict(_sealed_families(base))
    sealed = seal_followthrough_rows(None, (row,),
        (source, *families['trading_strategy_intent_v1']),
        (source_event, *base.events), (entry,))
    assert sealed[0]['strategy_number'] == 64
    assert restore_failure(sealed[0]) == value
    parent_intent = fixture(value, number=42)[0]
    assert parent_intent.intent_id != intent.intent_id
    assert parent_intent.reference_price == intent.reference_price
    assert parent_intent.quantity == intent.quantity


def test_sole_spread_delta_cannot_gain_fifty_early_exit():
    from test_strategy_forty_six_failure_route import witness
    from src.trading_runtime.strategy_followthrough_exit import validate_witness
    with pytest.raises(ValueError, match='pinned rule'):
        validate_witness(witness(), strategy_number=64)
