"""Exact normalized parent/child graph; component cold readers are seams."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from test_arte_structural_rejection_exit_v1 import prepared,PARENT
from test_profit_armed_structural_rejection_publication import BATCH
from src.trading_runtime.structural_rejection_exit_publication import prepare_native_structural_rejection_exit_rows
from src.trading_runtime.arte_intent_projection import project_strategy_intent
from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
from src.trading_runtime.profit_armed_structural_rejection_publication import _bound


def case(monkeypatch):
    evidence,confirmation,intent,context=prepared(monkeypatch)
    client=_bound(context)[1];owner=context.profile.owner
    # Component tests independently exercise actual source, financial and
    # graph readers. Here each seam records composition and ordering.
    entry=replace(intent,intent_id=confirmation.request.source_entry_intent_id,action='enter_long',
        reason='strategy_one_entry',reference_price=10.,invalidation_price=9.)
    recovered=SimpleNamespace(sequence=1,intent=entry,proposal=SimpleNamespace(assignment_id='A1',account_id='DU1'))
    calls=[]
    def financial(*a,**k): calls.append('financial')
    def manager(*a,**k):
        calls.append('manager')
        return SimpleNamespace(original_entry=entry,witness=confirmation.request.witness)
    def quote(*a,**k): calls.append('quote')
    monkeypatch.setattr('src.trading_runtime.structural_rejection_exit_financial_checkpoint.load_structural_rejection_exit_financial_checkpoint',financial)
    monkeypatch.setattr('src.trading_runtime.structural_rejection_exit_manager_checkpoint.load_structural_rejection_exit_manager_checkpoint',manager)
    monkeypatch.setattr('src.trading_runtime.structural_rejection_exit_quote_source.load_structural_rejection_exit_quote',quote)
    monkeypatch.setattr('src.trading_runtime.arte_strategy_one_entry_journal.load_committed_strategy_one_entry_page',
        lambda *a,**k:SimpleNamespace(entries=(recovered,),scanned_through_sequence=9,exhausted=True))
    core=project_strategy_intent(intent).core
    parent={**{key:value for key,value in core.items() if key!='event_time'},
        'record_id':PARENT,'run_id':confirmation.run_id,'batch_id':BATCH,'account_id':'DU1'}
    event=dict(record_id=PARENT,run_id=confirmation.run_id,batch_id=BATCH,
        account_id='DU1',sequence=10,event_time=core['event_time'])
    return client,dict(evidence.row),parent,event,dict(
        verified_prefix=V4CommittedPrefix(confirmation.run_id,9,BATCH,'cursor','running',(BATCH,)),
        first_price_source=owner.price_authority),calls,recovered


def test_composed_reader_checks_every_native_concern_then_complete_intent_hash(monkeypatch):
    client,row,parent,event,kw,calls,_=case(monkeypatch)
    result=prepare_native_structural_rejection_exit_rows(client,(row,),(parent,),(event,),**kw)
    assert dict(result[0])==row and calls==['financial','manager','quote']
    with pytest.raises(TypeError): result[0]['intent_hash']='changed'


@pytest.mark.parametrize('kind',('missing-parent','duplicate-parent','missing-event','row-hash','policy','entry','missing-entry'))
def test_composed_graph_never_accepts_a_partial_or_foreign_exit(monkeypatch,kind):
    client,row,parent,event,kw,calls,entry=case(monkeypatch)
    parents=(parent,);events=(event,)
    if kind=='missing-parent': parents=()
    elif kind=='duplicate-parent': parents=(parent,parent)
    elif kind=='missing-event': events=()
    elif kind=='row-hash': row['content_hash']='0'*64
    elif kind=='policy': parent['execution_deadline_ms']+=100
    elif kind=='entry': entry.sequence=9
    else:
        monkeypatch.setattr('src.trading_runtime.arte_strategy_one_entry_journal.load_committed_strategy_one_entry_page',
            lambda *a,**k:SimpleNamespace(entries=(),scanned_through_sequence=9,exhausted=True))
    with pytest.raises(ValueError):
        prepare_native_structural_rejection_exit_rows(client,(row,),parents,events,**kw)


def test_resealed_policy_and_intent_hash_cannot_replace_exact_rule_factory(monkeypatch):
    from src.trading_runtime.arte_intent_projection import ProjectedIntent,restore_strategy_intent
    from src.trading_runtime.arte_structural_rejection_exit_v1 import EXIT,_intent_hash
    from src.trading_runtime.arte_journal_writer import typed_row
    client,row,parent,event,kw,_,entry=case(monkeypatch)
    parent['execution_deadline_ms']+=100
    keys=set(project_strategy_intent(entry.intent).core)-{'event_time'}
    changed=restore_strategy_intent(ProjectedIntent(
        {**{key:parent[key] for key in keys},'event_time':event['event_time']},()))
    row['intent_hash']=_intent_hash(changed)
    row=typed_row(EXIT.name,{k:v for k,v in row.items() if k!='content_hash'})
    with pytest.raises(ValueError,match='factory intent'):
        prepare_native_structural_rejection_exit_rows(client,(row,),(parent,),(event,),**kw)

