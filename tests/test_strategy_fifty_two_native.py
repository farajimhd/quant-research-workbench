"""Declared52 actual scalar, native manager, normalized projection and cold OMS routes."""
import asyncio
from dataclasses import replace
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from src.trading_runtime.declared_profit_giveback import armed_profit_floor, declared_profit_giveback, ArmedProfitFloorPolicy
from src.trading_runtime.strategy_fifty_two_release import ARMED_PROFIT_FLOOR_POLICY as POLICY
from src.trading_runtime.strategy_profit_giveback import ProfitGivebackInput, profit_giveback
from src.trading_runtime.strategy_followthrough_failure import FollowThroughFailureInput
from src.trading_runtime.strategy_profit_giveback_exit import profit_giveback_exit_intent, validate_profit_giveback_witness
from src.trading_runtime.arte_profit_giveback_v4 import project_profit_giveback, restore_profit_giveback
from src.trading_runtime.strategy_profit_giveback_source import validate_profit_giveback_state
from src.trading_runtime.numbered_fixed_strategy import numbered_fixed_strategy
from test_strategy_profit_giveback import sample
from test_strategy_profit_giveback_exit import financial, ENTRY
from test_strategy_profit_giveback_source import fixture as state_fixture


def value():
    original = sample()
    return replace(original, completed=replace(original.completed,
        completed_five_second_close_int=107500, bid=10.75, ask=10.76))


def witness():
    return armed_profit_floor(value(), policy=POLICY)


def test_diagnostic_native_GPRO_scalars_reproduce_declared_additional_trigger():
    v = ProfitGivebackInput(FollowThroughFailureInput(50035000, 49786000, 1.32, 1.27,
        50035000, 13500, True, -.004354106620072962, .0010702399431633653,
        1.35, 1.35, 8191, 2610., False), 13700, 49860700)
    assert profit_giveback(v) is None
    assert armed_profit_floor(v, policy=POLICY) is not None
    assert declared_profit_giveback(v, strategy_number=50) is None
    assert declared_profit_giveback(v, strategy_number=52) == armed_profit_floor(v, policy=POLICY)


@pytest.mark.parametrize('change', [
    {'completed_five_second_close_int': 107501}, {'bid': 10.7500001},
    {'macd_signal': -.0001}, {'macd_signal': .2000001}, {'macd_line': .02},
    {'quote_age_us': 1000001}, {'quote_age_us': None}, {'price_valid': False},
    {'pending_exit': True}, {'position_quantity': 0.}, {'first_held_boundary_ms': 5100},
    {'completed_five_second_boundary_ms': 5000}, {'boundary_ms': 10100},
])
def test_missing_or_nonqualified_current_boundary_never_exits(change):
    v = value()
    assert armed_profit_floor(replace(v, completed=replace(v.completed, **change)), policy=POLICY) is None


@pytest.mark.parametrize('signal,line', [(0., -.01), (.2, .19)])
def test_signal_endpoints_inclusive_and_decimal_floor_exact(signal, line):
    v = value()
    assert armed_profit_floor(replace(v, completed=replace(v.completed,
        macd_signal=signal, macd_line=line)), policy=POLICY) is not None


def test_native_arm_uses_prior_high_not_current_high_or_time_alone():
    assert armed_profit_floor(replace(value(), prior_high_int=109999), policy=POLICY) is None
    for clock in (10000, 10100, 900):
        with pytest.raises(ValueError, match='prior causal'):
            armed_profit_floor(replace(value(), prior_high_through_boundary_ms=clock), policy=POLICY)
    assert declared_profit_giveback(sample(), strategy_number=50) == profit_giveback(sample())
    assert declared_profit_giveback(sample(), strategy_number=52) == profit_giveback(sample())


@pytest.mark.parametrize('fraction', [(True, 4), (3, 0), (4, 4), [3, 4]])
def test_invalid_declared_policy_rejected(fraction):
    with pytest.raises(ValueError):
        ArmedProfitFloorPolicy('test', fraction, (1, 50))


def projected():
    w = witness()
    exit_intent = profit_giveback_exit_intent(w, financial(), session_date=date(2026, 8, 4),
        source_entry_intent_id=ENTRY, strategy_number=52)
    identity = str(UUID(int=7))
    row = project_profit_giveback(w, exit_intent, financial(), session_date=date(2026, 8, 4),
        source_entry_intent_id=ENTRY, run_id='run', batch_id=identity, parent_record_id=identity,
        source_manager_snapshot_id=identity, source_manager_checkpoint_sequence=7, strategy_number=52)
    return exit_intent, row


def test_exact52_projection_restores_and_rejects_old50_floor():
    exit_intent, row = projected()
    assert restore_profit_giveback(row) == witness()
    assert exit_intent.reason == 'strategy_fifty_two_profit_giveback'
    assert exit_intent.metadata == {}
    for number in (42, 46, 47, 48, 50):
        with pytest.raises(ValueError, match='pinned rule'):
            restore_profit_giveback({**row, 'strategy_number': number})
    for field, change in [('prior_high_int', 109999), ('quote_age_us', 1000001),
                          ('completed_close_int', 107501), ('macd_signal', .200001)]:
        with pytest.raises(ValueError):
            restore_profit_giveback({**row, field: change})


def test_native_manager_state_binds_exact52_arm_and_original_entry():
    _, state, held = state_fixture(52)
    assert validate_profit_giveback_state(witness(), state, held) == state.submitted[0][1]
    key, source = state.submitted[0]
    for changed in (replace(state, submitted=((key, replace(source, strategy_number=50)),)),
                    replace(state, first_held_boundaries=((key, 1100),)),
                    replace(state, position_highs=((key, 109999),))):
        with pytest.raises(ValueError):
            validate_profit_giveback_state(witness(), changed, held)


@pytest.mark.parametrize('changed', [False, True])
def test52_saved_report_requires_exact_release(monkeypatch, changed):
    from test_numbered_trade_report_release_evidence import test_report_requires_exact_numbered_release_and_preserves_identity
    test_report_requires_exact_numbered_release_and_preserves_identity(monkeypatch, 52, changed)


def test52_real_certified_entry_activity_authority_mapping_and_foreign_number():
    from test_arte_episode_activity_v4 import graph37, seal
    args = graph37(52)
    sealed = seal(*args)
    assert sealed[0]['strategy_number'] == 52
    assert seal(sealed[0], *args[1:]) == sealed
    with pytest.raises(ValueError, match='another numbered entry'):
        seal(*args[:4], replace(args[4], strategy_number=50))


def test52_inherits50_early_failure_exactly():
    from test_strategy_fifty_failure_route import witness as early_witness
    from src.trading_runtime.strategy_followthrough_exit import validate_witness
    validate_witness(early_witness(), strategy_number=52)
    assert numbered_fixed_strategy(52).early_original_risk_policy == numbered_fixed_strategy(50).early_original_risk_policy
    assert numbered_fixed_strategy(50).armed_profit_floor_policy is None


@pytest.mark.parametrize('number', [50, 52])
@pytest.mark.parametrize('case', ['extension', 'original', 'unarmed', 'same_boundary', 'pending', 'strong_signal'])
def test_actual_manager_routes_only_confirmed_declared_floor_after_inherited_exits(number, case):
    from test_strategy_forty_two_management import prepared_manager
    from test_profit_arming_engine_boundary import reference
    from src.trading_runtime.strategy_profit_giveback_arm import profit_arm_candidate
    manager, old_witness, held, rows = prepared_manager(number, counts=(57, 18, 20, 10))
    manager.contract = numbered_fixed_strategy(number)
    key = (held.account_id, held.assignment_id, held.ticker)
    source = manager._submitted[key]
    at = old_witness.completed_five_second_boundary_ms
    # Retain the original causal native first-held clock from the fixture.
    manager._position_highs[key] = int(round((2 * source.reference_ask - source.initial_stop) * 10000)) + 1
    manager._profit_arm_financials[key] = held
    requests = manager.profit_arming_requests(boundary_ms=at - 100)
    arm = reference(requests[0][0])
    if case != 'unarmed':
        manager.accept_profit_arming_references(requests, (arm,), boundary_ms=at - 100)
    if case == 'same_boundary':
        manager._profit_arm_references[key] = replace(arm, candidate=replace(arm.candidate, boundary_ms=at))
    if case == 'pending':
        held = replace(held, pending_exit=True)
    risk = source.reference_ask - source.initial_stop
    price = int((source.reference_ask + (.5 if case == 'original' else .75) * risk) * 10000) - 1
    frame = rows(at, completed=True, age=48)
    signal = source.reference_ask / (40 if case == 'strong_signal' else 100)
    frame[5000].update(close_int=price, macd_line=signal - .001, macd_signal=signal)
    evidence = asyncio.run(manager.evidence.management_evidence(held.ticker, frame, boundary_ms=at))
    manager.evidence.management_evidence = AsyncMock(return_value=replace(evidence, bid=price / 10000, ask=price / 10000 + .01))
    asyncio.run(manager.on_management(held, frame, at))
    expected = case == 'original' or (case == 'extension' and number == 52)
    assert manager.runtime.submit_profit_giveback.await_count == int(expected)
    manager.runtime.submit_followthrough_failure.assert_not_awaited()
    manager.runtime.submit_confirmed_ah_failure.assert_not_awaited()
    if expected:
        _, w, _, actual_arm = manager.runtime.submit_profit_giveback.await_args.args
        assert actual_arm == arm
        validate_profit_giveback_witness(w, strategy_number=number)


def test_extension_exact52_cold_oms_and_order_reconstruction(monkeypatch):
    from src.trading_runtime.arte_oms_projection import _approved_strategy_one_oms_intent, reconstruct_strategy_one_oms_lineage
    from test_profit_giveback_oms_recovery import prepared
    import test_profit_giveback_oms_recovery as cold
    monkeypatch.setattr(cold, 'profit_giveback', lambda _: witness())
    group, source, history, reservation, decision, row = prepared(52)
    approved, _ = _approved_strategy_one_oms_intent(group, source, history, reservation, decision, profit_giveback_row=row)
    assert approved.reason == 'strategy_fifty_two_profit_giveback'
    assert reconstruct_strategy_one_oms_lineage(group, source, history, admission_reservation=reservation,
        admission_decision=decision, profit_giveback_row=row)
    with pytest.raises(ValueError, match='exact committed scalar witness'):
        _approved_strategy_one_oms_intent(group, source, history, reservation, decision,
            profit_giveback_row={**row, 'strategy_number': 50})



@pytest.mark.parametrize('corruption', [None, 'cursor_sequence', 'cursor_boundary', 'snapshot_id',
                                      'entry_account', 'entry_sequence', 'child_strategy', 'snapshot_strategy', 'snapshot_strategy42'])
def test52_extension_native_cold_checkpoint_requires_exact_ancestry(monkeypatch, corruption):
    # Reader-routing test with real types; mocks do not establish DB/Keeper attestation.
    from src.trading_runtime import arte_journal_projection as cursors
    from src.trading_runtime import strategy_one_management_snapshot as snapshots
    from src.trading_runtime import arte_followthrough_failure_v4 as entries
    from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
    from src.trading_runtime.strategy_profit_giveback_source import load_profit_giveback_checkpoint
    _, state, held = state_fixture(52)
    _, row = projected()
    batch = row['batch_id']
    prefix = V4CommittedPrefix('run', 20, batch, 'cursor', 'running', (batch,))
    cursor = dict(run_id='run', event_sequence=7, batch_id=batch, boundary_ms=9900, session_date='2026-08-04')
    snapshot = dict(run_id='run', snapshot_id=row['source_manager_snapshot_id'], checkpoint_sequence=7,
                    boundary_ms=9900, session_date='2026-08-04')
    entry = dict(batch_id=batch, action='enter_long', reason='strategy_one_entry', ticker=held.ticker,
                 reference_price=10., invalidation_price=9.)
    event = dict(account_id=held.account_id, sequence=6)
    child = dict(strategy_number=52, assignment_id=held.assignment_id, boundary_ms=900)
    if corruption == 'cursor_sequence': cursor['event_sequence'] = 6
    if corruption == 'cursor_boundary': cursor['boundary_ms'] = 9800
    if corruption == 'snapshot_id': snapshot['snapshot_id'] = str(UUID(int=9))
    if corruption == 'entry_account': event['account_id'] = 'foreign'
    if corruption == 'entry_sequence': event['sequence'] = 7
    if corruption == 'child_strategy': child['strategy_number'] = 50
    if corruption in ('snapshot_strategy', 'snapshot_strategy42'):
        key, proposal = state.submitted[0]
        state = replace(state, submitted=((key, replace(proposal, strategy_number=42 if corruption == 'snapshot_strategy42' else 50)),))
    calls = []
    def cursor_reader(client, ceiling):
        assert ceiling.last_sequence == 7 and ceiling.batch_ids == prefix.batch_ids
        calls.append('cursor')
        return cursor
    def snapshot_reader(client, **kwargs):
        assert kwargs == dict(run_id='run', checkpoint_sequence=7)
        calls.append('snapshot')
        return SimpleNamespace(snapshot=snapshot)
    def attach(client, committed, restored, **kwargs):
        assert committed is prefix and restored is state and kwargs['first_price_source'] == 'certified'
        calls.append('attach')
        return restored
    def source_reader(client, run, identity, **kwargs):
        assert kwargs['verified_prefix'] is prefix and identity == ENTRY
        calls.append('entry')
        return entry, event, child
    monkeypatch.setattr(cursors, 'load_latest_backtest_cursor', cursor_reader)
    monkeypatch.setattr(snapshots, 'load_unattested_manager_snapshot_rows', snapshot_reader)
    monkeypatch.setattr(snapshots, 'restore_manager_snapshot', lambda _: state)
    monkeypatch.setattr(snapshots, 'attach_committed_momentum_sources', attach)
    monkeypatch.setattr(entries, '_source_entry', source_reader)
    if corruption:
        with pytest.raises(ValueError):
            load_profit_giveback_checkpoint(object(), prefix, row, held, first_price_source='certified')
    else:
        assert load_profit_giveback_checkpoint(object(), prefix, row, held, first_price_source='certified') is state
        assert calls == ['cursor', 'snapshot', 'attach', 'entry']


def test52_exact_typed_transport_and_native_sealer_replay_extension(monkeypatch):
    from src.trading_runtime.arte_intent_projection import strategy_intent_batch
    from src.trading_runtime.arte_profit_giveback_v4 import V4ProfitGivebackBatch, seal_profit_giveback_rows
    from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
    from src.trading_runtime import arte_journal_writer as writer
    from src.trading_runtime import strategy_profit_giveback_source as authority
    exit_intent, _ = projected()
    base = strategy_intent_batch(exit_intent, run_id='run', run_month=date(2026, 8, 1),
        account_id=financial().account_id, attempt_id=str(UUID(int=2)), batch_id=str(UUID(int=3)),
        prior_batch_id=str(UUID(int=4)), sequence=10, source_cursor='cursor',
        run_status='running', recorded_at=exit_intent.event_time)
    row = project_profit_giveback(witness(), exit_intent, financial(), session_date=date(2026, 8, 4),
        source_entry_intent_id=ENTRY, run_id=base.run_id, batch_id=base.batch_id,
        parent_record_id=base.events[0]['record_id'], source_manager_snapshot_id=str(UUID(int=5)),
        source_manager_checkpoint_sequence=7, strategy_number=52)
    unit = V4ProfitGivebackBatch(base, row)
    assert unit.profit['strategy_number'] == 52
    with pytest.raises(ValueError): V4ProfitGivebackBatch(base, {**row, 'strategy_number': 50})
    parent = writer._canonical_typed_content('trading_strategy_intent_v1', dict(base.intents[0]))
    event = writer._canonical_typed_content('trading_event_v1', dict(base.events[0]))
    prefix = V4CommittedPrefix(base.run_id, 9, base.prior_batch_id, 'cursor', 'running', (base.prior_batch_id,))
    calls = []
    def checkpoint(client, committed, child, held, **kwargs):
        assert committed is prefix and child['strategy_number'] == 52
        _, state, _ = state_fixture(52)
        validate_profit_giveback_state(restore_profit_giveback(child), state, held)
        calls.append('source')
    monkeypatch.setattr(authority, 'load_profit_giveback_checkpoint', checkpoint)
    sealed = seal_profit_giveback_rows(object(), (row,), (parent,), (event,), prefix=prefix)
    assert len(sealed) == len(calls) == 1 and len(sealed[0]['content_hash']) == 64


def test_old50_scalar_replay_never_consults_optional52_rule(monkeypatch):
    import src.trading_runtime.declared_profit_giveback as optional
    monkeypatch.setattr(optional, 'declared_profit_giveback', lambda *a, **k: pytest.fail('old50 used optional52 rule'))
    validate_profit_giveback_witness(profit_giveback(sample()), strategy_number=50)
