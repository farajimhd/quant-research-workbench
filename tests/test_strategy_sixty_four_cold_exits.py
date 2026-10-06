"""64 native scalar/OMS components; no DB, producer attestation or financial run.

Every exit row is projected by its own 64 factory. Shared fixtures supply only
scalar observations and financial views, never relabeled committed source rows.
"""
from dataclasses import replace
from datetime import date
from uuid import UUID

import pytest

from src.trading_runtime.arte_oms_projection import (
    _approved_strategy_one_oms_intent, reconstruct_strategy_one_oms_lineage,
)
from src.trading_runtime.strategy_orders import canonical_runtime_order_raw
from test_profit_giveback_oms_recovery import prepared as profit_case


def case(kind):
    group, source, history, reservation, decision, row = profit_case(64)
    if kind == 'profit_giveback':
        return group, source, history, reservation, decision, row
    if kind == 'confirmed_ah':
        from test_arte_confirmed_ah_failure_v4 import prepared_case
        from src.trading_runtime.strategy_confirmed_ah_failure_exit import confirmed_ah_exit_intent as factory
        from src.trading_runtime.arte_confirmed_ah_failure_v4 import project_confirmed_ah_failure as project
        witness, held, _, _, _ = prepared_case()
        extra = {}
    else:
        from test_arte_liquidity_fade_failure_v4 import prepared_case
        from src.trading_runtime.strategy_liquidity_fade_exit import liquidity_fade_exit_intent as factory
        from src.trading_runtime.arte_liquidity_fade_failure_v4 import project_liquidity_fade_failure as project
        witness, held, _, _ = prepared_case()
        identity = str(UUID(int=64))
        extra = dict(source_build_id='a'*64, source_market_plan_token='b'*64,
            source_bars_attempt_id=identity, source_indicators_attempt_id=identity,
            source_liquidity_attempt_id=identity, source_manager_snapshot_id=identity,
            source_manager_checkpoint_sequence=7, source_manager_snapshot_hash='c'*64,
            source_broker_snapshot_id=identity, source_broker_snapshot_hash='d'*64)
    args = dict(session_date=date(2026, 8, 10),
        source_entry_intent_id=str(UUID(int=6401)), strategy_number=64)
    intent = factory(witness, held, **args)
    row = project(witness, intent, held, **args, run_id=history.run_id,
        batch_id=source.batch_id, parent_record_id=source.record_id, **extra)
    source = replace(source, account_id=held.account_id, intent=intent)
    group = replace(group, group={**group.group, 'strategy_intent_id': intent.intent_id},
        orders=(replace(group.orders[0], ticker=held.ticker, quantity=held.position_quantity,
            price=intent.reference_price),))
    reservation = {**reservation, 'intent_id': intent.intent_id, 'quantity': held.position_quantity}
    decision = {**decision, 'requested_quantity': held.position_quantity}
    return group, source, history, reservation, decision, row


@pytest.mark.parametrize('kind', ['profit_giveback', 'confirmed_ah', 'liquidity_fade'])
def test_exact64_exit_reconstructs_full_order_and_assignment(kind):
    group, source, history, reservation, decision, row = case(kind)
    assert row['strategy_number'] == group.group['strategy_revision'] == 64
    kwargs = {kind+'_row': row}
    approved, _ = _approved_strategy_one_oms_intent(group, source, history,
        reservation, decision, **kwargs)
    assert approved.metadata['assignment_id'] == reservation['assignment_id']
    assert replace(approved, metadata={}) == source.intent
    orders = reconstruct_strategy_one_oms_lineage(group, source, history,
        admission_reservation=reservation, admission_decision=decision, **kwargs)
    assert orders[0].raw == canonical_runtime_order_raw(group.orders[0], approved,
        run_id=history.run_id, strategy_id='early-squeeze-strategy', strategy_revision=64)


@pytest.mark.parametrize('kind', ['profit_giveback', 'confirmed_ah', 'liquidity_fade'])
@pytest.mark.parametrize('change', ['missing', 'foreign_parent', 'foreign_strategy',
    'entry', 'invalid_risk', 'assignment', 'resized', 'pending_clock'])
def test64_oms_rejects_changed_witness_or_financial_parent(kind, change):
    group, source, history, reservation, decision, row = case(kind)
    if change == 'missing':
        row = None
    elif change == 'resized':
        reservation = {**reservation, 'quantity': 1.}
    else:
        field, value = dict(foreign_parent=('parent_record_id', str(UUID(int=99))),
            foreign_strategy=('strategy_number', 42), entry=('source_entry_intent_id', str(UUID(int=99))),
            invalid_risk=('reference_ask', row['initial_stop']-.01),
            assignment=('assignment_id', 'foreign'),
            pending_clock=('first_held_boundary_ms', row['boundary_ms']+1))[change]
        row = {**row, field: value}
    with pytest.raises(ValueError):
        _approved_strategy_one_oms_intent(group, source, history, reservation, decision,
            **{kind+'_row': row})


@pytest.mark.parametrize('kind', ['confirmed_ah', 'liquidity_fade'])
def test_scalar_oms_boundary_does_not_attest_original_entry_ask(kind):
    """A valid predicate can retain the same order after an anchor edit.

    This scalar component receives no original-entry authority. Its acceptance
    does not certify that anchor: committed reader hashes and the management
    source validators must independently reject changed original prices.
    """
    group, source, history, reservation, decision, row = case(kind)
    changed = {**row, 'reference_ask': row['reference_ask']+.01}
    assert changed['reference_ask'] != row['reference_ask']
    original, _ = _approved_strategy_one_oms_intent(group, source, history,
        reservation, decision, **{kind+'_row': row})
    altered, _ = _approved_strategy_one_oms_intent(group, source, history,
        reservation, decision, **{kind+'_row': changed})
    assert altered == original


@pytest.mark.parametrize('kind', ['confirmed_ah', 'liquidity_fade'])
@pytest.mark.parametrize('change', [None, 'missing', 'foreign_parent', 'foreign_batch', 'tampered'])
def test64_native_scalar_cold_read_verifies_hash_and_prefix(kind, change):
    from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
    from src.trading_runtime.arte_journal_writer import typed_row
    from test_liquidity_fade_native_reader import Client
    if kind == 'confirmed_ah':
        from src.trading_runtime.arte_confirmed_ah_failure_v4 import CONFIRMED_AH_FAILURE as schema, load_confirmed_ah_failure as load
    else:
        from src.trading_runtime.arte_liquidity_fade_failure_v4 import LIQUIDITY_FADE_FAILURE as schema
        from src.trading_runtime.arte_liquidity_fade_reader_v4 import load_liquidity_fade_failure as load
    _, source, history, _, _, row = case(kind)
    stored = typed_row(schema.name, row)
    for name, dtype in schema.columns:
        if dtype.startswith('UInt'):
            stored[name] = str(stored[name])
    prefix = V4CommittedPrefix(history.run_id, 12, source.batch_id, 'cursor', 'running', (source.batch_id,))
    rows = [stored]
    if change == 'missing': rows = []
    elif change == 'foreign_parent': stored['parent_record_id'] = str(UUID(int=99))
    elif change == 'foreign_batch': stored['batch_id'] = str(UUID(int=99))
    elif change == 'tampered': stored['reference_ask'] += .01
    client = Client(rows)
    if change:
        with pytest.raises(RuntimeError): load(client, prefix, source.record_id)
    else:
        _, witness = load(client, prefix, source.record_id)
        original = witness.five_second if kind == 'confirmed_ah' else witness
        assert original.reference_ask == row['reference_ask']
        assert original.initial_stop == row['initial_stop']
    assert len(client.queries) == 1 and 'LIMIT 2 FORMAT JSONEachRow' in client.queries[0]


@pytest.mark.parametrize('change', [None, 'missing', 'foreign', 'future', 'risk'])
def test64_profit_management_retains_original_owned_entry_and_protection(change):
    from test_strategy_profit_giveback_source import fixture
    from src.trading_runtime.strategy_profit_giveback_source import validate_profit_giveback_state
    witness, state, held = fixture(strategy_number=64)
    key, proposal = state.submitted[0]
    assert proposal.strategy_number == 64
    if change == 'missing': state = replace(state, submitted=())
    elif change:
        field, value = dict(foreign=('account_id', 'other'),
            future=('boundary_ms', witness.first_held_boundary_ms),
            risk=('initial_stop', proposal.initial_stop-.1))[change]
        state = replace(state, submitted=((key, replace(proposal, **{field: value})),))
    if change:
        with pytest.raises(ValueError): validate_profit_giveback_state(witness, state, held)
    else:
        assert validate_profit_giveback_state(witness, state, held) is proposal
        assert (proposal.reference_ask, proposal.initial_stop) == (10., 9.)
        assert state.positions[0][1].stop == 9.


@pytest.mark.parametrize('change', [None, 'missing', 'foreign', 'future', 'risk', 'future_protection'])
def test64_liquidity_management_checks_owned_original_risk_and_first_held(change):
    from src.backend.backtest_strategy_one_management import StrategyOneManagementState
    from src.trading_runtime.strategy_one_stateful import StrategyOneEntryProposal
    from src.trading_runtime.strategy_one_position import ProtectionState
    from src.trading_runtime.strategy_liquidity_fade_source import validate_liquidity_fade_state
    from test_arte_liquidity_fade_failure_v4 import prepared_case
    witness, held, _, _ = prepared_case()
    key = held.account_id, held.assignment_id, held.ticker
    proposal = StrategyOneEntryProposal(held.assignment_id, held.account_id, held.ticker,
        44_780_000, 44_770_000, witness.reference_ask, witness.initial_stop,
        2.40, 'R1', .07, 44_779_000, 'S1', 64)
    protection = ProtectionState(witness.boundary_ms, witness.initial_stop, 2.40)
    state = StrategyOneManagementState(witness.boundary_ms, ((key, proposal),),
        ((key, protection),), (), ((key, 23300),), (), ((key, witness.first_held_boundary_ms),))
    if change == 'missing': state = replace(state, first_held_boundaries=())
    elif change == 'future_protection':
        state = replace(state, positions=((key, replace(protection, boundary_ms=state.boundary_ms+100)),))
    elif change:
        field, value = dict(foreign=('assignment_id', 'other'),
            future=('boundary_ms', witness.first_held_boundary_ms),
            risk=('reference_ask', proposal.reference_ask+.01))[change]
        state = replace(state, submitted=((key, replace(proposal, **{field: value})),))
    if change:
        with pytest.raises(ValueError): validate_liquidity_fade_state(witness, state, held)
    else:
        assert validate_liquidity_fade_state(witness, state, held) is proposal
        assert state.positions[0][1] is protection


def test64_real_manager_freezes_arm_and_preserves_submitted_entry_and_protection():
    from test_profit_arming_engine_boundary import manager_fixture, reference
    manager, _, state = manager_fixture(strategy_number=64)
    assert manager.contract.strategy_number == 64
    key, entry = state.submitted[0]
    protection = state.positions[0][1]
    requests = manager.profit_arming_requests(boundary_ms=state.boundary_ms)
    assert len(requests) == 1
    arm = reference(requests[0][0])
    manager.accept_profit_arming_references(requests, (arm,), boundary_ms=state.boundary_ms)
    manager._position_highs[key] = 120000
    assert manager.profit_arming_requests(boundary_ms=10000) == ()
    assert manager._profit_arm_references[key] is arm
    assert manager._submitted[key] is entry and manager._positions[key] is protection


def test64_real_manager_rejects_changed_capture_before_installing_arm():
    from test_profit_arming_engine_boundary import manager_fixture, reference
    manager, _, state = manager_fixture(strategy_number=64)
    requests = manager.profit_arming_requests(boundary_ms=state.boundary_ms)
    manager._position_highs[state.submitted[0][0]] += 1
    with pytest.raises(ValueError, match='differs from completed positions'):
        manager.accept_profit_arming_references(requests, (reference(requests[0][0]),),
            boundary_ms=state.boundary_ms)
    assert manager._profit_arm_references == {}
