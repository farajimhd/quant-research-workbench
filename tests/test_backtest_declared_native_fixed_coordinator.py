"""Actual shared scheduler path; callbacks do not certify native actor/finance."""
import asyncio
from dataclasses import replace
from copy import deepcopy

import numpy as np
import pytest

from test_backtest_declared_native_fixed_entry import parent, Source, financial, RUN
from test_strategy_one_stateful import _facts
from src.backend.backtest_declared_native_fixed_plan import load_declared_entry_source_plan
from src.backend.backtest_declared_native_fixed_entry import DeclaredEntryPreparation
from src.backend.backtest_declared_native_fixed_coordinator import run_declared_native_fixed_proposals, declared_decision_gate
from src.backend.backtest_strategy_one_scheduler import StrategyOneBoundaryScheduler
from src.trading_runtime.declared_native_entry_source import (
    DeclaredNativeEntrySourcePolicy, parse_declared_native_entry_source,
)
from src.trading_runtime.declared_native_fixed_candidate import QUOTE_SOURCE_CONTRACT

POLICY = DeclaredNativeEntrySourcePolicy(QUOTE_SOURCE_CONTRACT)


def candidates(source):
    original, *_ = _facts()
    from src.backend.backtest_declared_native_fixed_coordinator import _cursor_index
    indexed = _cursor_index(source)
    rows = []
    for i in np.flatnonzero(source.eligible_mask):
        ticker, boundary = source.parent.momentum.keys[int(i)]
        fact = source.parent.base.facts[int(i)]
        row = dict(original.market_row, session_date=source.parent.market.sessions[0],
                   ticker=ticker, boundary_ms=boundary,
                   bid_int=int(source.quote_columns[0][i]), ask_int=int(source.quote_columns[1][i]),
                   quote_timestamp_us=int(source.quote_columns[2][i]), quote_valid=int(source.quote_columns[3][i]))
        rows.append(replace(original, market_row=row, evidence=indexed[(ticker,boundary)]))
    return tuple(sorted(rows, key=lambda c:(c.market_row['boundary_ms'],c.market_row['ticker'])))


def run(source, views_by_id, *, rows=None, owned=False, on_proposal=None,
        financial_provider=None, roster=None, through=41000, after=0, policy=POLICY,
        calls=None, before=None):
    rows = candidates(source) if rows is None else rows
    rows = tuple(row for row in rows if after < row.market_row['boundary_ms'] <= through)
    scheduler = StrategyOneBoundaryScheduler(session_date=source.parent.market.sessions[0],
        candidate_rows=iter(rows), active_source=lambda *_:iter(()))
    roster = roster or {(v.ticker,*key):DeclaredEntryPreparation(RUN,key[1],key[0],source)
                       for key,v in views_by_id.items()}
    calls=[] if calls is None else calls; proposals=[]
    async def broker(work):calls.append(('broker',work.boundary_ms))
    async def view(ticker,boundary):
        calls.append(('views',boundary))
        return await financial_provider(ticker,boundary) if financial_provider else tuple(views_by_id.values())
    async def proposed(value):
        calls.append(('proposal',value.assignment_id,value.boundary_ms));proposals.append(value)
        if on_proposal:await on_proposal(value)
    async def manage(v,_rows,boundary):calls.append(('management',v.assignment_id,boundary))
    async def completed(work):calls.append(('observed',work.boundary_ms))
    async def finish(work):calls.append(('finish',work.boundary_ms))
    async def noop(*_):pass
    counts=asyncio.run(run_declared_native_fixed_proposals(scheduler,roster,
        source_policy=policy,through_boundary_ms=through,start_after_boundary_ms=after,
        process_broker_boundary=broker,financial_views=view,on_entry_proposal=proposed,
        on_management=manage,position_source_owned=lambda _:owned,
        financially_active_tickers=lambda:(),finish_boundary=finish,
        observe_activation=noop,observe_completed_seconds=completed,before_boundary=before))
    return counts,calls,proposals


def test_shared_scheduler_broker_observation_financial_proposal_order(parent):
    source=load_declared_entry_source_plan(parent,client=Source(parent))
    v=financial(parent);counts,calls,proposals=run(source,{(v.account_id,v.assignment_id):v})
    assert (counts.completed_boundaries,counts.candidate_decisions,counts.entry_proposals)==(2,2,2)
    assert calls==[(kind,b) if kind!='proposal' else (kind,'assignment',b)
                  for b in (31000,41000) for kind in ('broker','observed','views','proposal','finish')]
    prep=DeclaredEntryPreparation(RUN,v.assignment_id,v.account_id,source)
    assert all(prep.verify_proposal(p,v)==p for p in proposals)
    assert all(p.strategy_number==parent.capabilities.identity.strategy_number for p in proposals)


def test_refresh_after_callback_blocks_second_assignment_stale_financial_view(parent):
    source=load_declared_entry_source_plan(parent,client=Source(parent))
    first=replace(financial(parent),assignment_id='a')
    second=replace(first,assignment_id='b')
    views={(first.account_id,'a'):first,(second.account_id,'b'):second}
    async def entered(_):
        # The real callback must complete before a fresh view is obtained.
        views[(second.account_id,'b')]=replace(second,pending_capital_request=True)
    counts,calls,proposals=run(source,views,on_proposal=entered,through=31000)
    assert [p.assignment_id for p in proposals]==['a']
    assert counts.candidate_decisions==2 and counts.management_evaluations==1
    assert calls==[('broker',31000),('observed',31000),('views',31000),
                   ('proposal','a',31000),('views',31000),('management','b',31000),('finish',31000)]


def test_now_flat_owned_source_is_managed_before_same_bucket_reentry(parent):
    source=load_declared_entry_source_plan(parent,client=Source(parent));v=financial(parent)
    counts,calls,proposals=run(source,{(v.account_id,v.assignment_id):v},owned=True)
    assert not proposals and counts.candidate_decisions==0 and counts.management_evaluations==2
    assert ('management','assignment',31000) in calls


def test_original_sequential_pending_financial_checks_remain(parent):
    source=load_declared_entry_source_plan(parent,client=Source(parent))
    v=replace(financial(parent),pending_entry=True)
    counts,_,proposals=run(source,{(v.account_id,v.assignment_id):v})
    assert not proposals and counts.candidate_decisions==2 and counts.management_evaluations==2


@pytest.mark.parametrize('field,value', [('bid_int',True),('ask_int',1),('quote_timestamp_us',1),
    ('quote_valid',False),('session_date','2026-08-19')])
def test_actual_market_candidate_must_equal_independent_quote_source(parent,field,value):
    source=load_declared_entry_source_plan(parent,client=Source(parent));v=financial(parent)
    rows=list(candidates(source));rows[0]=replace(rows[0],market_row=dict(rows[0].market_row,**{field:value}))
    with pytest.raises(ValueError):run(source,{(v.account_id,v.assignment_id):v},rows=rows)


def test_scheduler_cannot_omit_an_admitted_source_candidate(parent):
    source=load_declared_entry_source_plan(parent,client=Source(parent));v=financial(parent)
    with pytest.raises(ValueError,match='unseen candidates'):
        run(source,{(v.account_id,v.assignment_id):v},rows=candidates(source)[:1])


def test_admitted_horizon_preserves_full_source_and_first_anchor(parent):
    source=load_declared_entry_source_plan(parent,client=Source(parent));v=financial(parent)
    gate=declared_decision_gate(source,through_boundary_ms=41000,start_after_boundary_ms=31000)
    assert [(f.ticker,f.boundary_ms) for f in gate.facts]==[('AAA',41000)]
    counts,_,proposals=run(source,{(v.account_id,v.assignment_id):v},after=31000)
    assert counts.entry_proposals==1 and proposals[0].source_token==source.token
    assert source.parent.first_indices.tolist()==[0,0]
    with pytest.raises(ValueError):gate.rejection_mask[0]=1


@pytest.mark.parametrize('through,after', [(True,0),(41000.0,0),(41001,0),(41000,True),(31000,41000)])
def test_declared_horizon_rejects_coercion_and_future_start(parent,through,after):
    source=load_declared_entry_source_plan(parent,client=Source(parent))
    with pytest.raises(ValueError):declared_decision_gate(source,through_boundary_ms=through,start_after_boundary_ms=after)


def test_foreign_roster_run_and_dynamic_roster_drift_fail_closed(parent):
    source=load_declared_entry_source_plan(parent,client=Source(parent));v=financial(parent)
    first=DeclaredEntryPreparation(RUN,'a',v.account_id,source)
    other=DeclaredEntryPreparation('22222222-2222-4222-8222-222222222222','b',v.account_id,source)
    with pytest.raises(ValueError,match='foreign run'):
        run(source,{(v.account_id,v.assignment_id):v},roster={(v.ticker,v.account_id,'a'):first,(v.ticker,v.account_id,'b'):other})
    async def wrong(*_):return (replace(v,account_id='foreign'),)
    with pytest.raises(ValueError,match='roster'):
        run(source,{(v.account_id,v.assignment_id):v},financial_provider=wrong)


@pytest.mark.parametrize('policy',[None,True,QUOTE_SOURCE_CONTRACT,{}])
def test_coordinator_requires_explicit_typed_source_declaration(parent,policy):
    source=load_declared_entry_source_plan(parent,client=Source(parent));v=financial(parent)
    with pytest.raises(ValueError,match='explicit typed'):
        run(source,{(v.account_id,v.assignment_id):v},policy=policy)


@pytest.mark.parametrize('change', ['unknown','scope','source','authority','missing'])
def test_complete_source_policy_is_immutable_and_exact(change):
    value=deepcopy(POLICY.payload())
    assert parse_declared_native_entry_source(value)==POLICY
    if change=='unknown':value['private_override']=True
    elif change=='missing':del value['source_population']
    else:value[{'scope':'source_population','source':'quote_product','authority':'authority'}[change]]='foreign'
    with pytest.raises(ValueError):parse_declared_native_entry_source(value)


def test_two_ticker_assignment_rosters_refresh_shared_state_in_one_boundary(parent):
    from test_backtest_declared_native_fixed_entry import changed_momentum
    from src.backend.backtest_declared_native_fixed_plan import compile_declared_momentum_plan
    c=replace(parent.candidates,
        prepared=(*parent.candidates.prepared,replace(parent.candidates.prepared[0],ticker='ZZZ')),
        coverage=(*parent.candidates.coverage,replace(parent.candidates.coverage[0],ticker='ZZZ')))
    e=replace(parent.entry,
        candidates=(*parent.entry.candidates,*(replace(f,ticker='ZZZ') for f in parent.entry.candidates)),
        activations=(*parent.entry.activations,*(replace(a,ticker='ZZZ') for a in parent.entry.activations)))
    m=replace(parent.market,units=(*parent.market.units,*(replace(u,ticker='ZZZ') for u in parent.market.units)))
    arrays=tuple(np.concatenate((getattr(parent.momentum,n),getattr(parent.momentum,n)))
                 for n in ('current_boundaries_ms','prior_boundaries_ms','current_line',
                           'current_signal','prior_line','prior_signal'))
    momentum=changed_momentum(parent,keys=(*parent.momentum.keys,('ZZZ',31000),('ZZZ',41000)),
                              arrays=arrays,attempts=parent.momentum.source_attempts*2)
    scope=compile_declared_momentum_plan(parent.capabilities,m,c,e,momentum)
    source=load_declared_entry_source_plan(scope,client=Source(scope))
    first=replace(financial(scope),assignment_id='a')
    second=replace(first,ticker='ZZZ',assignment_id='b')
    views={(first.account_id,'a'):first,(second.account_id,'b'):second}
    async def entered(_):views[(second.account_id,'b')]=replace(second,pending_capital_request=True)
    async def read(ticker,_):return tuple(v for v in views.values() if v.ticker==ticker)
    counts,calls,proposals=run(source,views,on_proposal=entered,financial_provider=read,through=31000)
    assert [p.ticker for p in proposals]==['AAA']
    assert counts.completed_boundaries==1 and counts.candidate_decisions==2
    assert counts.management_evaluations==1
    assert calls.count(('broker',31000))==1
    assert calls.index(('proposal','a',31000)) < calls.index(('management','b',31000))


@pytest.mark.parametrize("field,value", [("source_row_index",1),("source_row_index",0.0),
    ("stop_bar_boundary_ms",60000),("stop_low_int",1),
    ("macd_boundary_ms",(60000,60000,60000,60000))])
def test_full_candidate_cursor_must_match_independent_certified_columns(parent,field,value):
    source=load_declared_entry_source_plan(parent,client=Source(parent));v=financial(parent)
    rows=list(candidates(source));rows[0]=replace(rows[0],evidence=replace(rows[0].evidence,**{field:value}))
    with pytest.raises(ValueError,match="cursor differs"):
        run(source,{(v.account_id,v.assignment_id):v},rows=rows)


@pytest.mark.parametrize('change', ['quote', 'cursor'])
def test_candidate_source_rejection_precedes_every_external_callback(parent,change):
    source=load_declared_entry_source_plan(parent,client=Source(parent));v=financial(parent)
    rows=list(candidates(source));calls=[]
    if change=='quote':
        rows[0]=replace(rows[0],market_row=dict(rows[0].market_row,ask_int=1))
    else:
        rows[0]=replace(rows[0],evidence=replace(rows[0].evidence,stop_low_int=1))
    with pytest.raises(ValueError):
        run(source,{(v.account_id,v.assignment_id):v},rows=rows,calls=calls)
    assert calls==[]


def test_control_callback_cannot_change_quote_before_broker(parent):
    source=load_declared_entry_source_plan(parent,client=Source(parent));v=financial(parent)
    calls=[]
    async def changed(work):
        calls.append(('control',work.boundary_ms))
        work.candidate_rows[0].market_row['ask_int']=1
    with pytest.raises(ValueError):
        run(source,{(v.account_id,v.assignment_id):v},calls=calls,before=changed)
    assert calls==[('control',31000)]
