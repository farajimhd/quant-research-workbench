"""Actual own projection/restore with source certification and DB seams."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from test_arte_structural_rejection_exit_v1 import prepared
from test_profit_armed_structural_rejection_publication import BATCH
from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
from src.trading_runtime import profit_armed_structural_rejection_snapshot as snapshot
from src.trading_runtime.structural_rejection_exit_manager_checkpoint import load_structural_rejection_exit_manager_checkpoint
from src.trading_runtime import structural_rejection_exit_manager_checkpoint as checkpoint
from src.backend.backtest_profit_armed_structural_rejection_management import certified_structural_rejection_resistance


def case(monkeypatch):
    evidence,confirmed,intent,context=prepared(monkeypatch)
    owner=context.profile.owner
    state=owner.manager.capture_state(boundary_ms=25000)
    rows=snapshot.project_structural_rejection_snapshot(run_id=confirmed.run_id,
        session_date=owner.manager.runtime.config.anchor_date,checkpoint_sequence=9,state=state)
    monkeypatch.setattr(checkpoint,'load_unattested_structural_rejection_snapshot',lambda *a,**k:rows)
    # This test isolates manager/source replay. Full reader cardinality/hash
    # coverage is in test_structural_rejection_cold_snapshot.py.
    monkeypatch.setattr('src.backend.backtest_strategy_episode_activity_source.certified_episode_entry_intent',
        lambda *a,**k:SimpleNamespace(intent_id=confirmed.request.source_entry_intent_id))
    client=SimpleNamespace(structural_rejection_profile=context.profile)
    prefix=V4CommittedPrefix(confirmed.run_id,9,BATCH,'cursor','running',(BATCH,))
    args=(client,prefix,dict(evidence.row),confirmed.request.financial)
    return args,dict(first_price_source=owner.price_authority),owner,rows,confirmed


def test_own_historical_replay_survives_live_manager_retirement(monkeypatch):
    args,kwargs,owner,_,confirmed=case(monkeypatch)
    before=load_structural_rejection_exit_manager_checkpoint(*args,**kwargs)
    owner.complete_requests((confirmed.request,),boundary_ms=25000)
    owner.retire(('DU1','A1','AAA'))
    after=load_structural_rejection_exit_manager_checkpoint(*args,**kwargs)
    assert before==after and before.witness.quote.bid_int==104000
    assert before.original_entry.intent_id==args[2]['source_entry_intent_id']
    assert not owner._states and not owner._geometries


def test_cold_geometry_matches_live_first_cross_without_mutating_actor_state(monkeypatch):
    _,_,owner,_,_=case(monkeypatch)
    old=dict(owner._geometries)
    selected=certified_structural_rejection_resistance(owner,ticker='AAA',cross_boundary_ms=15000)
    assert selected[0]==owner._states[('DU1','A1','AAA')].resistance
    assert owner._geometries==old
    assert certified_structural_rejection_resistance(owner,ticker='AAA',cross_boundary_ms=10000) is None


@pytest.mark.parametrize('kind',('parent-hash','entry-id','bid','clock','source','missing-firing','retained-history'))
def test_replay_rejects_foreign_references_or_changed_certified_history(monkeypatch,kind):
    args,kwargs,owner,rows,_=case(monkeypatch)
    row=args[2]
    if kind=='parent-hash': row['source_manager_snapshot_hash']='0'*64
    elif kind=='entry-id': row['source_entry_intent_id']='00000000-0000-0000-0000-000000000098'
    elif kind=='bid': row['reference_bid_int']+=1
    elif kind=='clock': row['boundary_ms']+=100
    elif kind=='source': kwargs['first_price_source']=object()
    elif kind=='missing-firing':
        monkeypatch.setattr(checkpoint,'restore_structural_rejection_snapshot',lambda *a,**k:())
    else:
        from src.backend.backtest_profit_armed_structural_rejection_source import CompletedStructuralRejectionLookup
        original=CompletedStructuralRejectionLookup.bar_at
        def changed(self,ticker,boundary):
            value=original(self,ticker,boundary)
            return replace(value,trade_count=value.trade_count+1) if value is not None and boundary==10000 else value
        monkeypatch.setattr(CompletedStructuralRejectionLookup,'bar_at',changed)
    with pytest.raises(ValueError):
        load_structural_rejection_exit_manager_checkpoint(*args,**kwargs)
