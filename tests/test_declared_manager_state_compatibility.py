"""Inherited native consumers; scalar producers do not attest saved journals."""
from dataclasses import fields, replace
from types import SimpleNamespace

import pytest

from src.backend.backtest_strategy_one_management import (
    StrategyOneManagementState, OriginalRiskManagementState,
    inherited_management_state_type,
)
from src.trading_runtime.strategy_profit_giveback_arm import profit_arm_candidate
from src.trading_runtime.strategy_profit_giveback_source import validate_profit_giveback_state
from src.trading_runtime.strategy_liquidity_fade_source import validate_liquidity_fade_state
from test_strategy_profit_giveback_source import fixture


def selected(state):
    return OriginalRiskManagementState(**{f.name: getattr(state, f.name)
                                         for f in fields(StrategyOneManagementState)})


@pytest.mark.parametrize('number', [68, 69])
def test_actual_manager_arming_capture_preserves_inherited42_candidate(number):
    from test_profit_arming_engine_boundary import manager_fixture
    parent, held, state = manager_fixture(strategy_number=42)
    manager, actual_held, actual_state = manager_fixture(strategy_number=number)
    assert type(parent.capture_state(boundary_ms=state.boundary_ms)) is StrategyOneManagementState
    assert type(manager.capture_state(boundary_ms=actual_state.boundary_ms)) is OriginalRiskManagementState
    expected = parent.profit_arming_requests(boundary_ms=state.boundary_ms)
    actual = manager.profit_arming_requests(boundary_ms=actual_state.boundary_ms)
    assert actual == expected and actual[0][1] == held == actual_held
    manager.accept_profit_arming_references(actual,
        (__import__('test_profit_arming_engine_boundary').reference(actual[0][0]),),
        boundary_ms=actual_state.boundary_ms)
    assert manager.profit_arming_requests(boundary_ms=actual_state.boundary_ms) == ()


@pytest.mark.parametrize('number', [68, 69])
def test_selected_profit_and_half_risk_liquidity_scalar_sources_preserve42_facts(number):
    witness, parent, held = fixture(strategy_number=42)
    _, current, _ = fixture(strategy_number=number)
    captured = selected(current)
    assert profit_arm_candidate(captured, held, already_checkpointed=False) == profit_arm_candidate(parent, held, already_checkpointed=False)
    assert validate_profit_giveback_state(witness, captured, held) == current.submitted[0][1]
    from test_strategy_forty_two_liquidity_source_binding import held_case
    liquid, financial, baseline = held_case()
    key, source = baseline.submitted[0]
    typed = selected(replace(baseline, submitted=((key, replace(source, strategy_number=number)),)))
    assert validate_liquidity_fade_state(liquid, typed, financial) == typed.submitted[0][1]
    assert replace(typed.submitted[0][1], strategy_number=42) == validate_liquidity_fade_state(liquid, baseline, financial)


@pytest.mark.parametrize('defect', ['legacy_entry', 'allheld_entry', 'unknown', 'bool', 'foreign_subclass',
                                   'financial_identity', 'duplicate', 'missing', 'bad_pairs', 'bad_requests'])
def test_selected_state_needs_actual_registered_confirmed_entry(defect):
    witness, base, held = fixture(strategy_number=68)
    state = selected(base); key, source = state.submitted[0]
    if defect in ('legacy_entry', 'allheld_entry', 'unknown', 'bool'):
        number = {'legacy_entry':42, 'allheld_entry':66, 'unknown':9999, 'bool':True}[defect]
        state = replace(state, submitted=((key, replace(source, strategy_number=number)),))
    elif defect == 'foreign_subclass':
        class Foreign(OriginalRiskManagementState): pass
        state = Foreign(**{f.name:getattr(state, f.name) for f in fields(state)})
    elif defect == 'financial_identity': held = replace(held, assignment_id='foreign')
    elif defect == 'duplicate': state = replace(state, submitted=state.submitted * 2)
    elif defect == 'missing': state = replace(state, submitted=())
    elif defect == 'bad_pairs': state = replace(state, submitted=(('invalid',),))
    else: state = replace(state, original_risk_requests=[])
    assert not inherited_management_state_type(state, held)
    with pytest.raises(ValueError): profit_arm_candidate(state, held, already_checkpointed=False)
    with pytest.raises(ValueError): validate_profit_giveback_state(witness, state, held)


@pytest.mark.parametrize('field,value', [('reference_ask',10.01), ('initial_stop',8.9), ('ticker','FOREIGN')])
def test_selected_type_does_not_relax_original_profit_anchors(field, value):
    witness, base, held = fixture(strategy_number=68)
    key, source = base.submitted[0]
    state = selected(replace(base, submitted=((key, replace(source, **{field:value})),)))
    with pytest.raises(ValueError): validate_profit_giveback_state(witness, state, held)


@pytest.mark.parametrize('field', ['account_id', 'assignment_id', 'ticker'])
def test_all_three_selected_consumers_reject_foreign_entry_identity(field):
    witness, base, held = fixture(strategy_number=68)
    key, source = base.submitted[0]
    state = selected(replace(base, submitted=((key, replace(source, **{field:'FOREIGN'})),)))
    with pytest.raises(ValueError): profit_arm_candidate(state, held, already_checkpointed=False)
    with pytest.raises(ValueError): validate_profit_giveback_state(witness, state, held)
    from test_strategy_forty_two_liquidity_source_binding import held_case
    liquid, financial, baseline = held_case()
    liquid_key, liquid_source = baseline.submitted[0]
    typed = selected(replace(baseline, submitted=((liquid_key, replace(liquid_source,
        strategy_number=68, **{field:'FOREIGN'})),)))
    with pytest.raises(ValueError): validate_liquidity_fade_state(liquid, typed, financial)


@pytest.mark.parametrize('defect', ['undeclared_entry', 'foreign_subclass', 'original_ask', 'held_boundary'])
def test_liquidity_selected_type_keeps_policy_and_firstheld_authority(defect):
    from test_strategy_forty_two_liquidity_source_binding import held_case
    witness, held, base = held_case()
    key, source = base.submitted[0]
    state = selected(replace(base, submitted=((key, replace(source, strategy_number=68)),)))
    if defect=='undeclared_entry': state=selected(base)
    elif defect=='foreign_subclass':
        class Foreign(OriginalRiskManagementState): pass
        state=Foreign(**{f.name:getattr(state,f.name) for f in fields(state)})
    elif defect=='original_ask':
        state=replace(state, submitted=((key, replace(state.submitted[0][1],reference_ask=2.34)),))
    else: state=replace(state, first_held_boundaries=((key,witness.first_held_boundary_ms+100),))
    with pytest.raises(ValueError): validate_liquidity_fade_state(witness,state,held)


@pytest.mark.parametrize('corruption', [None, 'high', 'head'])
@pytest.mark.parametrize('number', [68, 69])
def test_cold_reference_reuses_selected_capture_and_retains_head_high_guards(monkeypatch, corruption, number):
    """External attested reader seam is controlled; this is not DB cold proof."""
    from src.trading_runtime import strategy_one_management_snapshot as snapshots
    from src.trading_runtime.strategy_profit_giveback_arm_reference import confirm_profit_arm_reference
    from src.backend.backtest_typed_publisher import TypedBacktestReceipt
    _, base, held = fixture(strategy_number=number)
    state = selected(base)
    candidate = profit_arm_candidate(state, held, already_checkpointed=False)
    head = snapshots.ManagerSnapshotHead('run', 7, 'batch', 'a'*64, 1)
    calls=[]
    class Keeper:
        def read_head(self, **kwargs):
            calls.append('head')
            return replace(head, keeper_version=2) if corruption=='head' and len(calls)>2 else head
    if corruption=='high': state=replace(state, position_highs=((state.position_highs[0][0],111000),))
    monkeypatch.setattr(snapshots, 'load_attested_manager_snapshot', lambda *a,**k:state)
    monkeypatch.setattr(snapshots, 'load_unattested_manager_snapshot_rows', lambda *a,**k:SimpleNamespace(snapshot={
        'snapshot_id':'14ff5fc6-1e02-4444-b2af-dd567da70f3c', 'content_hash':'a'*64, 'boundary_ms':9900}))
    if corruption:
        with pytest.raises(ValueError): confirm_profit_arm_reference(object(),Keeper(),candidate,held,TypedBacktestReceipt(7,'batch','cursor'),run_id='run')
    else:
        actual=confirm_profit_arm_reference(object(),Keeper(),candidate,held,TypedBacktestReceipt(7,'batch','cursor'),run_id='run')
        assert actual.candidate==candidate and actual.snapshot_hash==head.snapshot_hash
