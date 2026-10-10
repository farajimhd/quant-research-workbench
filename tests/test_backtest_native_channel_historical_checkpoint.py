"""Historical causal/OMS verification; database and entry-source I/O controlled."""
from dataclasses import replace

import pytest

from tests.test_backtest_native_channel_qualification import installed, unit  # noqa: F401
from tests.test_backtest_native_channel_financial_checkpoint import checkpoint_case
from src.backend.backtest_native_channel_financial_checkpoint import load_historical_native_channel_held_checkpoint


def test_historical_checkpoint_reproves_original_pending_request_without_current_head(installed,monkeypatch):
    scope=checkpoint_case(installed,monkeypatch,'head_changed',return_history=True)
    result=load_historical_native_channel_held_checkpoint(**scope)
    assert len(result)==1 and result[0].held_quantity==5
    assert result[0].checkpoint==scope['reference']
    assert result[0].request.source_plan is next(iter(scope['contexts'].values()))['prepared'].source_plan


@pytest.mark.parametrize('defect',['quantity','pending_exit','source_conid','original_entry',
    'execution_changed','execution_future','execution_missing','oms_fill_quantity','oms_state',
    'binding','context','cursor','prefix'])
def test_historical_checkpoint_keeps_native_source_and_financial_checks(installed,monkeypatch,defect):
    scope=checkpoint_case(installed,monkeypatch,defect,return_history=True)
    with pytest.raises(ValueError): load_historical_native_channel_held_checkpoint(**scope)


@pytest.mark.parametrize('field,value',[
    ('source_manager_checkpoint_sequence',13),
    ('source_manager_snapshot_id','aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'),
    ('source_manager_snapshot_hash','f'*64),
    ('source_broker_snapshot_id','bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'),
    ('source_broker_snapshot_hash','e'*64),
])
def test_historical_checkpoint_rejects_changed_saved_root_reference(installed,monkeypatch,field,value):
    scope=checkpoint_case(installed,monkeypatch,return_history=True)
    scope['reference']=replace(scope['reference'],**{field:value})
    with pytest.raises(ValueError): load_historical_native_channel_held_checkpoint(**scope)


def test_historical_checkpoint_rejects_incomplete_pending_evidence(installed,monkeypatch):
    scope=checkpoint_case(installed,monkeypatch,return_history=True)
    scope['contexts']={}
    with pytest.raises(ValueError): load_historical_native_channel_held_checkpoint(**scope)


@pytest.mark.parametrize('defect',[None,'execution_policy','batch','parent'])
def test_complete_historical_unit_reproof_retains_exact_envelope_and_intent(installed,monkeypatch,defect):
    from types import SimpleNamespace
    import json
    from tests.test_backtest_native_channel_confirmed_exit_unit import confirmed_unit_case
    from src.backend.backtest_native_channel_exit_unit import (
        prepare_confirmed_native_channel_exit_unit,reprove_historical_native_channel_exit_unit,
    )
    from src.trading_runtime.numbered_fixed_strategy import session_policy_payload
    live=confirmed_unit_case(installed,monkeypatch)
    packet=prepare_confirmed_native_channel_exit_unit(**live)
    historical=checkpoint_case(installed,monkeypatch,return_history=True)
    historical.pop('reference')
    policy=historical.pop('policy')
    monkeypatch.setattr('src.trading_runtime.numbered_fixed_strategy.numbered_fixed_strategy',
        lambda _n:SimpleNamespace(native_channel_management_policy=policy,
            policy_json=json.dumps({'session_policy':session_policy_payload()})))
    historical.update(management_policy=policy,unit=packet,**{key:live[key] for key in (
        'recorded_at','attempt_id','batch_id','parent_record_id','decision_record_id')})
    if defect=='execution_policy':
        intent={**packet.base.intents[0],'execution_policy_name':'adaptive_normal'}
        historical['unit']=replace(packet,base=replace(packet.base,intents=(intent,)))
    elif defect=='batch': historical['batch_id']='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
    elif defect=='parent': historical['parent_record_id']='bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
    if defect is None:
        assert reprove_historical_native_channel_exit_unit(**historical).sealed_families()==packet.sealed_families()
    else:
        with pytest.raises(ValueError): reprove_historical_native_channel_exit_unit(**historical)
