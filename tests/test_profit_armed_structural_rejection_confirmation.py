"""Real pending-request/INSERT/head handoff; financial readers are seams."""
from dataclasses import replace
from types import SimpleNamespace
import json

import pytest

from test_profit_armed_structural_rejection_publication import prepared,BATCH
from test_profit_armed_structural_rejection_management import RUN,DAY
from test_arte_typed_insert_dispatch import Stat
from src.trading_runtime import profit_armed_structural_rejection_publication as publication
from src.trading_runtime import profit_armed_structural_rejection_financial_checkpoint as finance
from src.trading_runtime import profit_armed_structural_rejection_confirmation as confirmation
from src.trading_runtime.profit_armed_structural_rejection_snapshot import PARENT,STATE
from src.trading_runtime.keeper_session import ManagedKeeperSession
from src.backend.backtest_market_data import market_day_boundary


def published(monkeypatch):
    client,context,_,_,prefix,cursor=prepared(monkeypatch)
    owner=context.profile.owner
    state=owner.manager.capture_state(boundary_ms=25000)
    families=publication.require_manager_publication(context)
    image=dict(portfolio={'DU1':{'state_hash':'d'*64}},
        broker={'snapshot':{'snapshot_id':'00000000-0000-0000-0000-000000000020','content_hash':'e'*64}},
        oms={'root':{'snapshot_id':'00000000-0000-0000-0000-000000000021','content_hash':'f'*64}})
    captured=finance.StructuralRejectionFinancialCapture(context.profile,state,9,json.dumps(image))
    def require(value,**kw):
        assert value is captured
        return image
    monkeypatch.setattr(finance,'require_financial_capture',require)
    checks=[]
    def verify(value,pub,**kw):
        assert value is captured and pub is context
        checks.append('financial')
        return object()
    monkeypatch.setattr(finance,'verify_financial_capture',verify)
    monkeypatch.setattr(finance,'require_financial_verification',lambda *a,**k:None)
    keeper=client.typed_insert_dispatch.keeper
    keeper.connected=True;keeper.client_state='CONNECTED';keeper.client_id=(1,b'fixture')
    keeper.add_listener=lambda callback:None
    keeper.exists=lambda path:keeper.rows[path][1] if path in keeper.rows else None
    session=ManagedKeeperSession(keeper);session._on_state('CONNECTED')
    def execute(sql,**kwargs):
        if sql.startswith('INSERT '): return ''
        table=sql.split(' FROM arte.',1)[1].split(' ',1)[0]
        return '\n'.join(json.dumps(row) for row in families[table])
    client.execute=execute
    publication.publish_manager_publication(client,session,context,captured)
    owner.manager.runtime.last_event_time=market_day_boundary(DAY,25000)
    return client,session,context,captured,owner.requests(boundary_ms=25000),families,checks,prefix,cursor


def test_confirmation_joins_complete_published_heads_and_exact_pending_request(monkeypatch):
    client,session,context,capture,requests,_,checks,*_=published(monkeypatch)
    result=confirmation.confirm_structural_rejection_exits(client,session,context,capture,requests)
    assert len(result)==1 and result[0].request is requests[0]
    assert confirmation.require_structural_rejection_confirmation(result[0],
        request=requests[0],runtime=context.profile.owner.manager.runtime) is result[0]
    refs=confirmation.structural_rejection_confirmation_reference(result[0])
    assert refs['source_manager_checkpoint_sequence']==9
    assert refs['source_portfolio_state_hash']=='d'*64
    assert len(refs)==8 and result[0].journal_batch_id==BATCH
    refs['source_portfolio_state_hash']='changed'
    assert confirmation.structural_rejection_confirmation_reference(result[0])['source_portfolio_state_hash']=='d'*64
    assert checks==['financial','financial']
    assert context.profile.owner.manager.runtime.calls==['entry']


@pytest.mark.parametrize('kind',('copy','field','runtime-frontier','completed-request'))
def test_confirmation_cannot_be_forged_reused_or_cross_a_later_frontier(monkeypatch,kind):
    client,session,context,capture,requests,*_=published(monkeypatch)
    issued=confirmation.confirm_structural_rejection_exits(client,session,context,capture,requests)[0]
    runtime=context.profile.owner.manager.runtime
    if kind=='copy': issued=replace(issued)
    elif kind=='field': object.__setattr__(issued,'checkpoint_sequence',10)
    elif kind=='runtime-frontier': runtime.last_event_time=market_day_boundary(DAY,25100)
    else: context.profile.owner.complete_requests(requests,boundary_ms=25000)
    with pytest.raises(ValueError,match='Unissued|changed|mutated'):
        confirmation.require_structural_rejection_confirmation(issued,request=requests[0],runtime=runtime)


@pytest.mark.parametrize('kind',('manager-head','source-cursor','child-readback','financial'))
def test_confirmation_requires_all_four_independent_persisted_gates(monkeypatch,kind):
    client,session,context,capture,requests,families,_,prefix,cursor=published(monkeypatch)
    if kind=='manager-head':
        path=publication.selected_manager_head_path(RUN)
        raw,stat=session.client.rows[path]
        session.client.rows[path]=(raw[:-64]+b'0'*64,Stat(stat.version+1))
    elif kind=='source-cursor': cursor['boundary_ms']=25100
    elif kind=='child-readback': families[STATE.name][0]['original_ask_int']+=1
    else:
        def fail(*a,**k): raise ValueError('complete financial inventory changed')
        monkeypatch.setattr(finance,'verify_financial_capture',fail)
    with pytest.raises(ValueError,match='head|cursor|readback|financial'):
        confirmation.confirm_structural_rejection_exits(client,session,context,capture,requests)


def test_own_exit_factory_preserves_inherited_execution_and_deterministic_identity(monkeypatch):
    from src.trading_runtime.profit_armed_structural_rejection_exit import structural_rejection_exit_intent,REASON
    from src.trading_runtime.execution_policies import ExecutionPolicyName,PartialFillPolicy
    client,session,context,capture,requests,*_=published(monkeypatch)
    issued=confirmation.confirm_structural_rejection_exits(client,session,context,capture,requests)[0]
    intent=structural_rejection_exit_intent(issued)
    assert structural_rejection_exit_intent(issued)==intent
    assert intent.action=='exit' and intent.quantity==10. and intent.reference_price==10.4
    assert intent.reason==REASON and intent.metadata=={} and intent.outside_rth is True
    assert intent.event_time==market_day_boundary(DAY,25000)
    assert intent.execution_policy.name is ExecutionPolicyName.ADAPTIVE_URGENT
    assert intent.execution_policy.partial_fill_policy is PartialFillPolicy.COMPLETE_REMAINDER
    assert context.profile.owner.manager.runtime.calls==['entry']
    with pytest.raises(ValueError,match='Unissued'): structural_rejection_exit_intent(replace(issued))
