"""Prepared ordinary exit dispatch and immutable journal route, with mocked OMS."""
import asyncio
from dataclasses import replace

import pytest

from src.trading_runtime.strategy_engine import StrategyEvaluation
from src.trading_runtime.strategy_liquidity_fade_exit import liquidity_fade_exit_intent
from test_liquidity_fade_runtime_emission import runtime_case, submit
from test_strategy_forty_two_liquidity_source_binding import source_case


def case():
    runtime,data=runtime_case(42)
    witness,*_=source_case()
    data['witness']=witness
    data['intent']=liquidity_fade_exit_intent(witness,data['financial'],
        session_date=data['session_date'],source_entry_intent_id=data['source_entry_intent_id'],
        strategy_number=42)
    decision,_=runtime.portfolio.approve.return_value
    runtime.portfolio.approve.return_value=decision,replace(data['intent'],
        metadata={'assignment_id':data['financial'].assignment_id})
    return runtime,data


def test_half_risk_exit_uses_portfolio_oms_and_keeps_exact_witness():
    runtime,data=case()
    try:
        asyncio.run(submit(runtime,data))
        runtime.portfolio.approve.assert_awaited_once_with(data['intent'],
            account_id=data['financial'].account_id,assignment_id=data['financial'].assignment_id)
        runtime.order_manager.submit_intent.assert_awaited_once()
        record,=runtime.journal.unfenced_records()
        stored=runtime.journal.liquidity_fade_exit_for_record(record.record_id)
        assert stored[1] is data['witness']
        assert stored[5]==data['observation_source']
        assert record.payload['strategy_revision']==42
    finally:
        runtime.journal.close()


def test_numbered41_never_enters_legacy_deferred_request_cleanup():
    from unittest.mock import Mock
    runtime, data = case()
    runtime.portfolio.has_pending_entry_requests = Mock(return_value=True)
    runtime.portfolio.withdraw_invalidated_requests = Mock()
    try:
        asyncio.run(submit(runtime, data))
        runtime.portfolio.has_pending_entry_requests.assert_not_called()
        runtime.portfolio.withdraw_invalidated_requests.assert_not_called()
        runtime.order_manager.submit_intent.assert_awaited_once()
    finally:
        runtime.journal.close()


@pytest.mark.parametrize('change',['parent_revision','pending','missing_checkpoint','future_checkpoint','generic'])
def test_unbound_half_risk_exit_never_reaches_order_authority(change):
    runtime,data=case()
    try:
        if change=='parent_revision':runtime.config.strategy_revision=38
        elif change=='pending':data['financial']=replace(data['financial'],pending_exit=True)
        elif change=='missing_checkpoint':del data['observation_source']['source_manager_snapshot_hash']
        elif change=='future_checkpoint':data['observation_source']['source_manager_checkpoint_sequence']=65
        if change=='generic':
            operation=runtime._execute_intents(StrategyEvaluation(intents=(data['intent'],)),
                data['financial'].account_id,None)
        else:operation=submit(runtime,data)
        with pytest.raises(ValueError):asyncio.run(operation)
        runtime.portfolio.approve.assert_not_awaited()
        runtime.order_manager.submit_intent.assert_not_awaited()
        assert runtime.journal.pending_record_count==0
    finally:
        runtime.journal.close()


@pytest.mark.parametrize('failure',[None,'financial','checkpoint','confirmation'])
def test_controller_keeps_checkpoint_fence_before_numbered_submission(monkeypatch,failure):
    from test_liquidity_fade_manager_checkpoint_route import test_controller_fences_and_confirms_before_submitting
    test_controller_fences_and_confirms_before_submitting(monkeypatch,failure,42)


def oms_case():
    from test_liquidity_fade_inherited_exit_routes import oms_case as inherited_case
    from test_arte_liquidity_fade_failure_v4 import prepared_case
    from src.trading_runtime.arte_liquidity_fade_failure_v4 import project_liquidity_fade_failure
    from datetime import date
    group,source,history,reservation,decision,template=inherited_case('liquidity',38)
    witness,*_=source_case()
    _,financial,_,_=prepared_case()
    intent=liquidity_fade_exit_intent(witness,financial,session_date=date(2026,8,10),
        source_entry_intent_id=template['source_entry_intent_id'],strategy_number=42)
    fields=('source_build_id','source_market_plan_token','source_bars_attempt_id',
        'source_indicators_attempt_id','source_liquidity_attempt_id','source_manager_snapshot_id',
        'source_manager_checkpoint_sequence','source_manager_snapshot_hash',
        'source_broker_snapshot_id','source_broker_snapshot_hash')
    row=project_liquidity_fade_failure(witness,intent,financial,session_date=date(2026,8,10),
        source_entry_intent_id=template['source_entry_intent_id'],run_id=history.run_id,
        batch_id=source.batch_id,parent_record_id=source.record_id,strategy_number=42,
        **{key:template[key] for key in fields})
    source=replace(source,intent=intent)
    group=replace(group,group=dict(group.group,strategy_revision=42,strategy_intent_id=intent.intent_id),
        orders=(replace(group.orders[0],price=intent.reference_price),))
    reservation=dict(reservation,intent_id=intent.intent_id)
    return group,source,history,reservation,decision,row


def test_cold_oms_reconstruction_retains_exact_half_risk_order_and_assignment():
    from src.trading_runtime.arte_oms_projection import reconstruct_strategy_one_oms_lineage, _approved_strategy_one_oms_intent
    from src.trading_runtime.strategy_orders import canonical_runtime_order_raw
    group,source,history,reservation,decision,row=oms_case()
    orders=reconstruct_strategy_one_oms_lineage(group,source,history,
        admission_reservation=reservation,admission_decision=decision,liquidity_fade_row=row)
    approved,_=_approved_strategy_one_oms_intent(group,source,history,reservation,decision,
        liquidity_fade_row=row)
    assert approved.metadata['assignment_id']==reservation['assignment_id']
    assert approved.intent_id==source.intent.intent_id
    assert approved.reference_price==source.intent.reference_price
    assert approved.quantity==source.intent.quantity
    assert orders[0].raw==canonical_runtime_order_raw(group.orders[0],approved,
        run_id=history.run_id,strategy_id='early-squeeze-strategy',strategy_revision=42)


@pytest.mark.parametrize('field,value',[('strategy_number',38),('bid',2.3001),
    ('source_entry_intent_id','00000000-0000-0000-0000-000000000001'),('assignment_id','foreign')])
def test_changed_half_risk_oms_witness_cannot_reconstruct_order(field,value):
    from src.trading_runtime.arte_oms_projection import reconstruct_strategy_one_oms_lineage
    group,source,history,reservation,decision,row=oms_case()
    with pytest.raises(ValueError):
        reconstruct_strategy_one_oms_lineage(group,source,history,admission_reservation=reservation,
            admission_decision=decision,liquidity_fade_row=dict(row,**{field:value}))
