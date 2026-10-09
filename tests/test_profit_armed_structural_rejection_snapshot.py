"""Issued actual manager capture→normalized graph→cold replay; no DB transport."""
import asyncio
from dataclasses import replace
from copy import deepcopy
import math

import pytest

from test_profit_armed_structural_rejection_management import fixture, start, financial, quote, KEY, RUN, DAY
from src.trading_runtime import profit_armed_structural_rejection_snapshot as snapshot
from src.trading_runtime.profit_armed_structural_rejection_checkpoint import StructuralRejectionCheckpointBinding, _tree
from src.backend.backtest_profit_armed_structural_rejection_management import _plain


def packet(monkeypatch,clocks=(10000,15000,20000,25000)):
    manager,owner,calls=fixture(monkeypatch);start(manager)
    # Source certification seams are explicit in the manager fixture. Keep
    # the real inherited scalar projection and protection validation here.
    import src.backend.backtest_strategy_certified_price_break as price
    import src.backend.backtest_strategy_episode_activity_source as activity
    monkeypatch.setattr(price,'certified_price_entry_intent',lambda *args,**kwargs:None)
    monkeypatch.setattr(activity,'certified_episode_activity_witness',lambda *args,**kwargs:None)
    async def run():
        for clock in clocks:
            await manager.on_management(financial(),quote(owner,clock),clock)
    asyncio.run(run())
    boundary=clocks[-1] if clocks else 1000
    state=manager.capture_state(boundary_ms=boundary)
    rows=snapshot.project_structural_rejection_snapshot(run_id=RUN,session_date=DAY,checkpoint_sequence=9,state=state)
    current=owner._states[KEY]
    bindings={KEY:StructuralRejectionCheckpointBinding(current.source,current.policy,current.position_intent_id,
        current.account_id,current.session_origin_us,current.first_held_boundary_ms,current.original_ask_int,
        current.original_stop_int,9,current.last_decision_boundary_ms)}
    geometries={KEY:_plain(owner._geometries[KEY])} if KEY in owner._geometries else {}
    kwargs=dict(expected_bindings=bindings,declaration=owner.declaration,expected_geometries=geometries)
    return manager,owner,rows,kwargs


def reseal(rows,**changes):
    rows=replace(rows,**changes)
    root={k:v for k,v in rows.snapshot.items() if k!='content_hash'}
    for prefix,family in zip(snapshot._PREFIXES,(rows.states,rows.bars,rows.links,rows.levels)):
        root[prefix+'_count']=len(family)
        root[prefix+'_hash']=snapshot._digest([r['content_hash'] for r in family])
    return replace(rows,snapshot=snapshot._sealed(snapshot.PARENT,root))


def changed(contract,row,**changes):
    return snapshot._sealed(contract,{**{k:v for k,v in row.items() if k!='content_hash'},**changes})


@pytest.mark.parametrize('clocks',((),(10000,),(10000,15000),(10000,15000,20000),(10000,15000,20000,25000)),
                         ids=('initial','armed','crossed','one-rejection','fired'))
def test_actual_manager_capture_normalized_cold_replay(monkeypatch,clocks):
    manager,owner,rows,kwargs=packet(monkeypatch,clocks)
    result=snapshot.restore_structural_rejection_snapshot(rows,**kwargs)
    assert _tree(result[0][1])==_tree(owner._states[KEY])
    assert _tree(result[0][3])==_tree(owner._firing.get(KEY))
    assert result[0][4]==kwargs['expected_geometries'].get(KEY)
    assert rows.snapshot['protection_hash']==rows.inherited.snapshot['protection_hash']
    assert rows.states[0]['source_interval_plan_token']==owner.intervals.token
    assert all('payload' not in name for table in snapshot.TABLES for name,_ in table.columns)
    assert all("storage_policy = 'live_market_ssd'" in table.ddl() for table in snapshot.TABLES)
    assert manager.runtime.calls==['entry']


@pytest.mark.parametrize('family',('states','bars','links','levels'))
def test_resealed_duplicate_keys_rejected(monkeypatch,family):
    _,_,rows,kwargs=packet(monkeypatch)
    values=getattr(rows,family)
    forged=reseal(rows,**{family:(values[0],*values)})
    with pytest.raises(ValueError,match='repeat|order'):
        snapshot.restore_structural_rejection_snapshot(forged,**kwargs)


@pytest.mark.parametrize('family',('states','bars','links','levels'))
def test_missing_child_rejected_even_resealed(monkeypatch,family):
    _,_,rows,kwargs=packet(monkeypatch)
    forged=reseal(rows,**{family:getattr(rows,family)[1:]})
    with pytest.raises(ValueError): snapshot.restore_structural_rejection_snapshot(forged,**kwargs)


@pytest.mark.parametrize('field,value',(('ordinal',255),('bar_role','foreign'),('state_kind','future'),('boundary_ms',30000)))
def test_link_mutation_reaches_graph_guard(monkeypatch,field,value):
    _,_,rows,kwargs=packet(monkeypatch)
    link=changed(snapshot.LINK,rows.links[0],**{field:value})
    forged=reseal(rows,links=tuple(sorted((link,*rows.links[1:]),key=lambda r:
        (r['account_id'],r['assignment_id'],r['ticker'],r['state_kind'],r['bar_role'],r['ordinal']))))
    with pytest.raises(ValueError): snapshot.restore_structural_rejection_snapshot(forged,**kwargs)


@pytest.mark.parametrize('field,value',(('source_build_id','foreign'),('original_ask_int',99999),
    ('cursor_status','processed-future'),('has_firing_witness',0),('fired',0),('activity_count',2)))
def test_resealed_state_source_anchor_policy_status_mutation(monkeypatch,field,value):
    _,_,rows,kwargs=packet(monkeypatch)
    states=tuple(changed(snapshot.STATE,r,**{field:value}) if r['state_kind']=='current' else r for r in rows.states)
    with pytest.raises(ValueError):
        snapshot.restore_structural_rejection_snapshot(reseal(rows,states=states),**kwargs)


@pytest.mark.parametrize('field,value',(('price_int',105999),('lower_int',105899),('confirmed_epoch_ms',2**63),
    ('reference_basis','upper'),('decoded_seed_hash','b'*64)))
def test_resealed_level_mutation_rejected_by_independent_geometry(monkeypatch,field,value):
    _,_,rows,kwargs=packet(monkeypatch)
    levels=(changed(snapshot.LEVEL,rows.levels[0],**{field:value}),)
    with pytest.raises(ValueError):
        snapshot.restore_structural_rejection_snapshot(reseal(rows,levels=levels),**kwargs)


@pytest.mark.parametrize('table,field,value',((snapshot.BAR,'boundary_ms',-1),(snapshot.BAR,'trade_count',2**64),
    (snapshot.LINK,'ordinal',True),(snapshot.STATE,'fired',256),(snapshot.STATE,'source_bars_attempt_id','foreign')))
def test_exact_schema_type_range_rejection(monkeypatch,table,field,value):
    _,_,rows,_=packet(monkeypatch)
    family=rows.bars if table is snapshot.BAR else rows.links if table is snapshot.LINK else rows.states
    with pytest.raises(ValueError): changed(table,family[0],**{field:value})


def test_unknown_column_and_native_integer_macd_fail_closed(monkeypatch):
    _,_,rows,_=packet(monkeypatch)
    with pytest.raises(ValueError,match='exact contract'):
        changed(snapshot.BAR,rows.bars[0],unrelated=1)
    with pytest.raises(ValueError,match='Float64'):
        snapshot._bits(1)
    for value in (None,float('nan'),float('inf'),-0.,0.,.25):
        decoded=snapshot._float(snapshot._bits(value))
        assert decoded is None if value is None else math.isnan(decoded) if math.isnan(value) else snapshot._bits(decoded)==snapshot._bits(value)


def test_foreign_cursor_binding_and_missing_geometry_fail_closed(monkeypatch):
    _,_,rows,kwargs=packet(monkeypatch)
    bad={**kwargs,'expected_bindings':{KEY:replace(kwargs['expected_bindings'][KEY],checkpoint_sequence=10)}}
    with pytest.raises(ValueError,match='cursor'):
        snapshot.restore_structural_rejection_snapshot(rows,**bad)
    with pytest.raises(ValueError,match='independent certified'):
        snapshot.restore_structural_rejection_snapshot(rows,**{**kwargs,'expected_geometries':{}})


def test_capture_copy_is_not_native_projection_authority(monkeypatch):
    manager,owner,_,_=packet(monkeypatch,())
    state=manager.capture_state(boundary_ms=1000)
    with pytest.raises(ValueError,match='Unissued'):
        snapshot.project_structural_rejection_snapshot(run_id=RUN,session_date=DAY,checkpoint_sequence=9,state=replace(state))


def test_delayed_projection_preserves_capture_after_actual_manager_retirement(monkeypatch):
    manager,owner,expected,kwargs=packet(monkeypatch)
    captured=manager.capture_state(boundary_ms=25000)
    requests=owner.requests(boundary_ms=25000)
    assert requests
    owner.complete_requests(requests,boundary_ms=25000)
    asyncio.run(manager.on_management(financial(held=0),{},26000))
    assert KEY not in owner._states
    assert KEY not in owner._geometries
    assert KEY not in owner._firing
    delayed=snapshot.project_structural_rejection_snapshot(
        run_id=RUN,session_date=DAY,checkpoint_sequence=9,state=captured)
    assert delayed==expected
    restored=snapshot.restore_structural_rejection_snapshot(delayed,**kwargs)
    assert restored[0][3] is not None
    assert restored[0][4]==kwargs['expected_geometries'][KEY]
