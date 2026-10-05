"""Exact numbered intent and normalized scalar replay for declared AH failure."""
from dataclasses import replace
from datetime import date
from uuid import UUID

import pytest

from src.trading_runtime.arte_followthrough_failure_v4 import (
    project_followthrough_failure, restore_failure, seal_followthrough_rows,
    validate_numbered_failure,
)
from src.trading_runtime.arte_intent_projection import strategy_intent_batch
from src.trading_runtime.arte_journal_writer import _sealed_families
from src.trading_runtime.declared_followthrough_failure import declared_followthrough_failure
from src.trading_runtime.strategy_followthrough_failure import FollowThroughFailure, FollowThroughFailureInput
from src.trading_runtime.strategy_followthrough_exit import followthrough_exit_intent, validate_witness
from src.trading_runtime.strategy_forty_six_release import EARLY_FAILURE_POLICY
from src.trading_runtime.strategy_one_stateful import StrategyOneFinancialView
from src.trading_runtime.strategy_engine import AssignmentStatus, StrategyPermissions
from src.trading_runtime.strategy_zero_regime_risk_failure import zero_regime_risk_failure


def witness():
    return FollowThroughFailure(43_225_000, 43_211_100, 10.01, 9.89, 99_700,
                               -.02, -.01, 9.97, 9.98, 100_000)


def input_for(w):
    return FollowThroughFailureInput(w.boundary_ms, w.first_held_boundary_ms,
        w.reference_ask, w.initial_stop, w.boundary_ms, w.completed_close_int, True,
        w.macd_line, w.macd_signal, w.bid, w.ask, w.quote_age_us, 10., False)


def fixture(w, number=46):
    financial = StrategyOneFinancialView('assignment-1', 'DU1', 'AAA',
        AssignmentStatus.WATCHING, StrategyPermissions(observe=True, enter=True),
        10., False, False, False, 1)
    source_id = str(UUID(int=77))
    intent = followthrough_exit_intent(w, financial, session_date=date(2026, 8, 18),
        source_entry_intent_id=source_id, strategy_number=number)
    base = strategy_intent_batch(intent, run_id=str(UUID(int=1)), run_month=date(2026, 8, 1),
        account_id='DU1', attempt_id=str(UUID(int=2)), batch_id=str(UUID(int=3)),
        prior_batch_id=str(UUID(int=0)), sequence=2, source_cursor='cursor',
        run_status='running', recorded_at=intent.event_time)
    row = project_followthrough_failure(w, intent, source_id, run_id=base.run_id,
        batch_id=base.batch_id, parent_record_id=base.events[0]['record_id'],
        assignment_id=financial.assignment_id, strategy_number=number)
    families = dict(_sealed_families(base))
    source = {**families['trading_strategy_intent_v1'][0], 'record_id': str(UUID(int=10)),
        'intent_id': source_id, 'action': 'enter_long', 'reason': 'strategy_one_entry',
        'reference_price': 10.01, 'invalidation_price': 9.89}
    source_event = {**base.events[0], 'record_id': source['record_id'], 'sequence': 1}
    entry = {'parent_record_id': source['record_id'], 'strategy_number': number,
        'assignment_id': financial.assignment_id, 'boundary_ms': w.first_held_boundary_ms - 100}
    return intent, base, row, source, source_event, entry


def test_declared_extension_composes_inherited_rule_first():
    value = input_for(witness())
    assert zero_regime_risk_failure(value) is None
    assert declared_followthrough_failure(value, inherited=zero_regime_risk_failure,
        early_policy=EARLY_FAILURE_POLICY) == witness()
    sentinel = replace(witness(), completed_close_int=99_400, bid=9.94)
    assert declared_followthrough_failure(value, inherited=lambda _: sentinel,
        early_policy=EARLY_FAILURE_POLICY) is sentinel
    assert declared_followthrough_failure(value, inherited=zero_regime_risk_failure) is None


def test_extension_witness_has_exact_46_intent_and_normalized_roundtrip():
    w = witness()
    intent, base, row, source, source_event, entry = fixture(w)
    families = dict(_sealed_families(base))
    sealed = seal_followthrough_rows(None, (row,),
        (source, *families['trading_strategy_intent_v1']), (source_event, *base.events), (entry,))
    assert len(sealed) == 1 and sealed[0]['strategy_number'] == 46
    assert restore_failure(sealed[0]) == w
    assert intent.reason == 'strategy_nine_followthrough_failure'
    assert intent.metadata == {} and intent.quantity == 10 and intent.reference_price == w.bid
    for old in (9, 42):
        with pytest.raises(ValueError, match='pinned rule'):
            validate_numbered_failure(w, old)
        with pytest.raises(ValueError, match='pinned rule'):
            restore_failure({**sealed[0], 'strategy_number': old})


@pytest.mark.parametrize('w', [
    FollowThroughFailure(40_000, 31_100, 10.01, 9.89, 99_700, -.02, -.01, 9.97, 9.98, 100_000),
    FollowThroughFailure(43_225_000, 43_211_100, 10.01, 9.89, 99_400, -.02, -.01, 9.94, 9.95, 100_000),
    FollowThroughFailure(43_280_000, 43_211_100, 10.01, 9.89, 99_400, -.02, -.01, 9.94, 9.95, 100_000),
])
def test_inherited_parent_witness_preserved_but_intent_identity_is_numbered(w):
    validate_witness(w, strategy_number=42)
    validate_witness(w, strategy_number=46)
    prior, *_ = fixture(w, 42)
    current, base, row, source, source_event, entry = fixture(w, 46)
    assert prior.intent_id != current.intent_id
    assert replace(current, intent_id=prior.intent_id) == prior
    families = dict(_sealed_families(base))
    forged = {**families['trading_strategy_intent_v1'][0], 'intent_id': prior.intent_id}
    event = {**base.events[0], 'entity_id': prior.intent_id}
    with pytest.raises(ValueError, match='immutable factory'):
        seal_followthrough_rows(None, (row,), (source, forged), (source_event, event), (entry,))


@pytest.mark.parametrize('changes', [
    {'completed_close_int': 99_900, 'bid': 9.99},
    {'macd_line': .02, 'macd_signal': .01},
    {'quote_age_us': 1_000_001},
    {'boundary_ms': 43_280_000},
    {'boundary_ms': 43_225_100},
])
def test_extension_rejects_nonqualifying_witness(changes):
    with pytest.raises(ValueError, match='pinned rule'):
        validate_witness(replace(witness(), **changes), strategy_number=46)


@pytest.mark.parametrize('number', [43, 44, 45, True])
def test_unpublished_or_noninteger_numbers_cannot_use_legacy_fallback(number):
    with pytest.raises(ValueError):
        validate_numbered_failure(witness(), number)
