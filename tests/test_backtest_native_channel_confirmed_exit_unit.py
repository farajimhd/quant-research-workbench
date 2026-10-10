"""Complete native held/causal preparation with controlled checkpoint I/O."""
from types import SimpleNamespace
import json

import pytest

from tests.test_backtest_native_channel_qualification import installed, unit  # noqa: F401
from tests.test_backtest_native_channel_financial_checkpoint import checkpoint_case
from src.backend.backtest_native_channel_exit_unit import prepare_confirmed_native_channel_exit_unit
from src.trading_runtime.numbered_fixed_strategy import session_policy_payload


def confirmed_unit_case(installed,monkeypatch,defect=None):
    scope=checkpoint_case(installed,monkeypatch,defect,return_scope=True)
    policy=scope['management_policy']
    monkeypatch.setattr('src.trading_runtime.numbered_fixed_strategy.numbered_fixed_strategy',
        lambda number:SimpleNamespace(native_channel_management_policy=policy if number==1 else None,
            policy_json=json.dumps({'session_policy':session_policy_payload()})))
    key=next(iter(scope['contexts']))
    evidence=scope['contexts'][key]
    scope.update(pending_record_id=key,recorded_at=evidence['as_of'],
        attempt_id='99999999-9999-4999-8999-999999999999',
        batch_id='77777777-7777-4777-8777-777777777777',
        parent_record_id='66666666-6666-4666-8666-666666666666',
        decision_record_id='55555555-5555-4555-8555-555555555555')
    return scope


def test_preparation_derives_complete_next_unit_from_confirmed_native_hold(installed,monkeypatch):
    scope=confirmed_unit_case(installed,monkeypatch)
    packet=prepare_confirmed_native_channel_exit_unit(**scope)
    assert packet.base.first_sequence==scope['receipt'].last_sequence+1
    assert packet.base.prior_batch_id==scope['receipt'].last_batch_id
    assert packet.base.source_cursor==scope['receipt'].source_cursor
    assert packet.base.intents[0]['quantity']=='5.000000000000000000'
    assert packet.decision['source_manager_checkpoint_sequence']==scope['receipt'].last_sequence
    assert packet.decision['batch_id']==scope['batch_id']
    assert len(packet.sources)>0 and len(packet.executions)>0


@pytest.mark.parametrize('defect',['quantity','pending_exit','source_conid','original_entry',
    'execution_changed','execution_future','execution_missing','oms_fill_quantity','oms_state',
    'binding','head_changed','frontier_changed'])
def test_complete_preparation_rejects_changed_or_foreign_native_authority(installed,monkeypatch,defect):
    scope=confirmed_unit_case(installed,monkeypatch,defect)
    with pytest.raises(ValueError): prepare_confirmed_native_channel_exit_unit(**scope)


def test_preparation_cannot_select_a_context_outside_complete_pending_inventory(installed,monkeypatch):
    scope=confirmed_unit_case(installed,monkeypatch)
    scope['pending_record_id']='foreign'
    with pytest.raises(ValueError): prepare_confirmed_native_channel_exit_unit(**scope)
