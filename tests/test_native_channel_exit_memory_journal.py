"""Real memory journal buffers complete channel claims without granting authority."""
from dataclasses import replace

import pytest

from tests.test_backtest_native_channel_qualification import installed, unit  # noqa: F401
from tests.test_backtest_native_channel_confirmed_exit_unit import confirmed_unit_case
from src.backend.backtest_native_channel_exit_unit import (
    prepare_confirmed_native_channel_exit_request,native_channel_exit_intent,
)
from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.trading_runtime.native_channel_exit_journal_context import NativeChannelExitJournalContext
from src.trading_runtime.strategy_one_contract import STRATEGY_ID


def journal_case(installed,monkeypatch,*,certified_source=False):
    from src.trading_runtime.numbered_fixed_strategy import numbered_fixed_strategy
    from src.trading_runtime.strategy_registry import initialize_numbered_fixed_strategies
    initialize_numbered_fixed_strategies()
    scope=confirmed_unit_case(installed,monkeypatch)
    from src.trading_runtime import numbered_fixed_strategy as module
    fixture_contract=module.numbered_fixed_strategy(1)
    monkeypatch.setattr(module,'numbered_fixed_strategy',
        lambda number:fixture_contract if number==1 else numbered_fixed_strategy(number))
    if certified_source:
        from tests.test_backtest_strategy_first_price_source import authority,Bars
        from src.backend.backtest_strategy_first_price_source import load_first_price_source
        from src.backend.backtest_strategy_certified_price_break import (
            CertifiedPriceReadbackAuthority,compile_certified_price_break_plan,
        )
        market,parent=authority()
        scope['first_price_source']=CertifiedPriceReadbackAuthority(scope['run_id'],
            compile_certified_price_break_plan(load_first_price_source(market,parent,client=Bars())))
    packet,request=prepare_confirmed_native_channel_exit_request(**scope)
    context=NativeChannelExitJournalContext(packet,scope['management_policy'],scope['contexts'],
        scope['first_price_source'],**{key:scope[key] for key in (
            'recorded_at','attempt_id','batch_id','parent_record_id','decision_record_id')})
    intent=native_channel_exit_intent(request,strategy_number=1,management_policy=context.policy)
    journal=BacktestMemoryJournal(run_id=packet.base.run_id,max_pending_records=1,
        initial_sequence=packet.base.first_sequence-1)
    return journal,dict(intent=intent,request=request,context=context,
        strategy_id=STRATEGY_ID,strategy_revision=1)


def test_pending_channel_source_has_exact_identity_retry_and_fence_cleanup(installed,monkeypatch):
    journal,scope=journal_case(installed,monkeypatch)
    record=journal.append_native_channel_exit(**scope)
    assert record.sequence==scope['context'].unit.base.first_sequence
    assert record.entity_id==scope['intent'].intent_id and record.payload['metadata']=={}
    assert journal.append_native_channel_exit(**scope) is record
    assert journal.native_channel_exit_for_record(record.record_id)==(
        scope['request'],scope['context'],STRATEGY_ID,1)
    assert len(journal.unfenced_records())==1
    with pytest.raises(RuntimeError):
        journal.append(run_id=journal.run_id,category='test',entity_type='test',entity_id='overflow',payload={})
    journal.mark_fenced(record.sequence)
    assert journal.native_channel_exit_for_record(record.record_id) is None
    assert not journal._native_channel_intents
    journal.close()


@pytest.mark.parametrize('defect',['quantity','metadata','strategy','context','frontier','retry_context'])
def test_channel_buffer_rejects_foreign_or_changed_claims(installed,monkeypatch,defect):
    journal,scope=journal_case(installed,monkeypatch)
    if defect=='quantity': scope['intent']=replace(scope['intent'],quantity=scope['intent'].quantity+1)
    elif defect=='metadata': scope['intent']=replace(scope['intent'],metadata={'permission':True})
    elif defect=='strategy': scope['strategy_id']='foreign'
    elif defect=='context': scope['context']=object()
    elif defect=='frontier': journal._next_sequence+=1
    elif defect=='retry_context':
        journal.append_native_channel_exit(**scope)
        scope['context']=replace(scope['context'],recorded_at=scope['context'].recorded_at.replace(microsecond=1))
    with pytest.raises(ValueError): journal.append_native_channel_exit(**scope)
    assert len(journal._native_channel_exits)==int(defect=='retry_context')
    journal.close()
