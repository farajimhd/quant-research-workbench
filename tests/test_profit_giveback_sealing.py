from copy import deepcopy
import pytest
from src.trading_runtime import arte_journal_writer as writer
from src.trading_runtime.arte_profit_giveback_v4 import PROFIT_GIVEBACK,seal_profit_giveback_rows
from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
from test_profit_giveback_typed_batch import unit


@pytest.mark.parametrize('corruption',[None,'event_time','reason','quantity','intent_id','source_checkpoint','missing','record_id'])
@pytest.mark.parametrize('number', [31, 32])
def test_sealer_revalidates_real_canonical_factory_and_source_route(monkeypatch,corruption,number):
    """Test-only schema registration and source-reader mock; no native writes."""
    from src.trading_runtime import strategy_profit_giveback_source as source
    monkeypatch.setitem(writer._CONTRACTS,PROFIT_GIVEBACK.name,PROFIT_GIVEBACK)
    base,row=unit(strategy_number=number)
    parent=writer._canonical_typed_content('trading_strategy_intent_v1',dict(base.intents[0]))
    event=writer._canonical_typed_content('trading_event_v1',dict(base.events[0]))
    parent=deepcopy(parent);event=deepcopy(event)
    prefix=V4CommittedPrefix(base.run_id,9,base.prior_batch_id,'cursor','running',(base.prior_batch_id,))
    calls=[]
    def check(client,committed,child,financial,**kwargs):
        assert committed is prefix and child['source_manager_checkpoint_sequence']==7
        calls.append(financial)
    monkeypatch.setattr(source,'load_profit_giveback_checkpoint',check)
    if corruption=='event_time':event['event_time']='2026-08-04 08:00:15.000000'
    if corruption=='reason':parent['reason']='other'
    if corruption=='quantity':parent['quantity']=0
    if corruption=='intent_id':parent['intent_id']='00000000-0000-0000-0000-000000000099'
    if corruption=='source_checkpoint':row['source_manager_checkpoint_sequence']=10
    if corruption=='record_id':row['record_id']='00000000-0000-0000-0000-000000000099'
    if corruption:
        with pytest.raises(ValueError):seal_profit_giveback_rows(object(),() if corruption=='missing' else (row,),(parent,),(event,),prefix=prefix)
    else:
        sealed=seal_profit_giveback_rows(object(),(row,),(parent,),(event,),prefix=prefix)
        assert len(sealed)==len(calls)==1 and len(sealed[0]['content_hash'])==64


@pytest.mark.parametrize('change', [{'run_id':'other'},{'last_sequence':10},{'last_sequence':6},
                                   {'status':'completed'},{'last_batch_id':'other'},{'batch_ids':()}])
def test_current_future_or_uncommitted_prefix_cannot_seal(monkeypatch,change):
    from dataclasses import replace
    monkeypatch.setitem(writer._CONTRACTS,PROFIT_GIVEBACK.name,PROFIT_GIVEBACK)
    base,row=unit()
    parent=writer._canonical_typed_content('trading_strategy_intent_v1',dict(base.intents[0]))
    event=writer._canonical_typed_content('trading_event_v1',dict(base.events[0]))
    prefix=V4CommittedPrefix(base.run_id,9,base.prior_batch_id,'cursor','running',(base.prior_batch_id,))
    with pytest.raises(ValueError,match='preceding prefix'):
        seal_profit_giveback_rows(object(),(row,),(parent,),(event,),prefix=replace(prefix,**change))
