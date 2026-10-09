"""Actual manager callbacks; persisted certifiers/registration are explicit seams.

These fixtures are component qualification, not an installed release or DB run.
"""
import asyncio
from datetime import date
from dataclasses import replace
from types import SimpleNamespace, MappingProxyType

import polars as pl
import pytest

from src.backend import backtest_profit_armed_structural_rejection_management as native
from src.backend.backtest_strategy_one_management import StrategyOneManagementRunner, StructuralRejectionManagementState
from src.backend.backtest_profit_armed_structural_rejection_source import CompletedStructuralRejectionLookup, SCHEMA
from src.backend.backtest_market_data import CertifiedMarketDayPlan, MarketDayUnit, ExecutionInterval, market_day_boundary
from src.backend.backtest_strategy_one_v7_interval_store import CertifiedV7IntervalPlan, CertifiedV7IntervalUnit
from src.backend.structural_v7_seed import CertifiedSeedPlan
from src.backend.backtest_strategy_certified_price_break import CertifiedInitialPriceBreakPlan, CertifiedPriceReadbackAuthority
from src.trading_runtime.profit_armed_structural_rejection_native_policy import (
    NativeStructuralRejectionDeclaration, native_structural_rejection_declaration, RULE, SOURCE_INPUT)
from src.trading_runtime.numbered_fixed_strategy import numbered_fixed_strategy
from src.trading_runtime.strategy_one_v7_intervals import V7LevelInterval
from src.trading_runtime.strategy_one_management_evidence import StrategyOneManagementEvidence
from src.trading_runtime.strategy_one_stateful import StrategyOneEntryProposal, StrategyOneFinancialView
from src.trading_runtime.strategy_engine import AssignmentStatus, StrategyPermissions

DAY=date(2026,8,18)
RUN='00000000-0000-0000-0000-000000000010'
ENTRY='00000000-0000-0000-0000-000000000011'
KEY=('DU1','A1','AAA')


class Contract:
    def __init__(self):
        self.parent=numbered_fixed_strategy(57)
        self.release=SimpleNamespace(rule_set_contracts=(RULE,),input_contracts=(SOURCE_INPUT,))
        self.profit_armed_structural_rejection_policy=NativeStructuralRejectionDeclaration()
    def __getattr__(self,name): return getattr(self.parent,name)


class Runtime:
    def __init__(self):
        self.config=SimpleNamespace(strategy_revision=57,strategy_id=1,anchor_date=DAY)
        self.run_id=RUN; self.calls=[]
    async def submit_strategy_one_proposal(self,proposal):
        self.calls.append('entry')
        return ({'order_group':{'group_id':'G1'},'decision':{'status':'approved'}},)
    async def submit_strategy_one_add(self,*args,**kwargs): raise AssertionError('No add expected')
    async def submit_strategy_one_protection(self,*args,**kwargs): raise AssertionError('No protection amendment expected')
    async def submit_numbered_session_exit(self,*args): self.calls.append('liquidation')


class Evidence:
    async def management_evidence(self,ticker,resolutions,*,boundary_ms):
        # Missing ordinary management quote avoids an unrelated protection
        # command. New quote authority comes from the real scheduler row.
        return StrategyOneManagementEvidence(ticker,boundary_ms,None,None,False,None,None,(),())


def financial(held=10,pending_exit=False):
    return StrategyOneFinancialView('A1','DU1','AAA',AssignmentStatus.MANAGING,
        StrategyPermissions(observe=True,enter=True),held,False,pending_exit,False,1)


def fixture(monkeypatch,*,rows=None,levels=None):
    contract=Contract()
    import src.trading_runtime.numbered_fixed_strategy as contracts
    monkeypatch.setattr(contracts,'numbered_fixed_strategy',lambda number:contract)
    monkeypatch.setattr(contracts,'resolve_numbered_fixed_strategy',lambda *args:contract)
    manager=StrategyOneManagementRunner(runtime=Runtime(),evidence=Evidence(),tick_for_ticker=lambda _: .01)
    manager._liquidity_lookup=SimpleNamespace(window_at=lambda *args:None)
    market=CertifiedMarketDayPlan(ExecutionInterval('time',100),'owned-build','a'*64,(DAY.isoformat(),),('AAA',),
        tuple(MarketDayUnit('owned-build',DAY.isoformat(),'AAA',stage,
            f'00000000-0000-0000-0000-00000000000{i}','b'*64,4,'c'*64)
            for i,stage in enumerate(('bars','technical','broker_100ms'),1)),(100,5000,10000),'d'*64)
    origin=round(market_day_boundary(DAY,0).timestamp()*1000)
    if levels is None:
        levels=(V7LevelInterval('R1',1,1000,50000,10.59,10.61,'resistance','',origin+1000,False),)
    coverage=CertifiedV7IntervalUnit('AAA',RUN,market.units[0].attempt_id,'e'*64,'f'*64,'a'*64,'b'*64,'fixture',40,len(levels),'c'*64,'d'*64)
    intervals=CertifiedV7IntervalPlan(market.build_id,DAY.isoformat(),(coverage,),
        (('AAA',tuple(range(1000,50000,1000))),),(('AAA',levels),),'e'*64)
    seeds=CertifiedSeedPlan(market.build_id,'f'*64,({'ticker':'AAA','session_date':'2026-08-17',
        'available_at':'2026-08-18 07:00:00+00:00','source_checkpoint_hash':'e'*64,'level_count':1},),'a'*64,False)
    # Explicit price authority seam; real certification remains mandatory in
    # production, and these are never used as installed release certificates.
    price_plan=object.__new__(CertifiedInitialPriceBreakPlan)
    object.__setattr__(price_plan,'source',SimpleNamespace(market=market))
    authority=CertifiedPriceReadbackAuthority(RUN,price_plan)
    if rows is None:
        rows=((10000,105000,110000,100),(15000,107000,110000,100),
              (20000,105000,107000,20),(25000,104000,106000,20))
    frame=pl.DataFrame([dict(source_build_id=market.build_id,source_market_plan_token=market.token,
        source_bars_attempt_id=market.units[0].attempt_id,source_indicators_attempt_id=market.units[1].attempt_id,
        source_liquidity_attempt_id=market.units[2].attempt_id,session_date=DAY.isoformat(),ticker='AAA',
        resolution_ms=5000,boundary_ms=clock,close_int=close,high_int=high,trade_count=count,
        price_valid=True,extremes_valid=True,macd_line=-.2,macd_signal=-.1)
        for clock,close,high,count in rows],schema=SCHEMA)
    calls=[]
    monkeypatch.setattr(native,'verify_market_day_plan',lambda *args:calls.append('market'))
    monkeypatch.setattr(native,'certified_seed_plan',lambda *args:(calls.append('seeds') or seeds))
    monkeypatch.setattr(native,'certify_v7_interval_plan',lambda *args,**kwargs:(calls.append('intervals') or intervals))
    monkeypatch.setattr(native,'load_completed_structural_rejection_lookup',lambda *args,**kwargs:
        (calls.append('bars') or CompletedStructuralRejectionLookup(frame,**kwargs)))
    monkeypatch.setattr(native,'certified_episode_entry_intent',lambda *args,**kwargs:
        (calls.append('entry_authority') or SimpleNamespace(intent_id=ENTRY)))
    owner=native.prepare_native_structural_rejection_manager(manager,object(),market=market,seeds=seeds,
        intervals=intervals,price_authority=authority,through_boundary_ms=40000)
    manager.bind_structural_rejection_management(owner)
    return manager,owner,calls


def quote(owner,clock,**changes):
    return {100:dict(session_date=DAY.isoformat(),ticker='AAA',resolution_ms=100,boundary_ms=clock,
        quote_valid=1,quote_timestamp_us=owner.origin_us+clock*1000,bid_int=104000,ask_int=104100,**changes)}


def start(manager):
    async def run():
        await manager.on_entry_proposal(StrategyOneEntryProposal('A1','DU1','AAA',100,.0,10.,9.,12.,'ENTRY-R',.5,100,'S1',strategy_number=57))
        await manager.on_management(financial(),{},1000)
    asyncio.run(run())


def prepared_source(monkeypatch):
    previous,owner,calls=fixture(monkeypatch)
    source=native.prepare_structural_rejection_source(object(),contract=previous.contract,
        strategy_id=previous.runtime.config.strategy_id,strategy_revision=previous.runtime.config.strategy_revision,
        run_id=RUN,session_date=DAY,market=owner.market,seeds=owner.seeds,intervals=owner.intervals,
        price_authority=owner.price_authority,through_boundary_ms=40000)
    return source,calls


def test_source_preparation_precedes_actual_manager_and_binding_does_not_recertify(monkeypatch):
    source,calls=prepared_source(monkeypatch)
    before=tuple(calls)
    manager=StrategyOneManagementRunner(runtime=Runtime(),evidence=Evidence(),tick_for_ticker=lambda _: .01)
    owner=native.bind_prepared_structural_rejection_manager(manager,source)
    manager.bind_structural_rejection_management(owner)
    assert tuple(calls)==before
    assert owner.lookup is source.lookup and owner.price_authority is source.price_authority
    assert owner.market is source.market and owner.intervals is source.intervals


@pytest.mark.parametrize('field',('strategy_revision','run_id','anchor_date','strategy_id'))
def test_prepared_source_refuses_foreign_runtime(monkeypatch,field):
    source,_=prepared_source(monkeypatch)
    runtime=Runtime()
    if field=='run_id': runtime.run_id='foreign'
    else: setattr(runtime.config,field,58 if field=='strategy_revision' else date(2026,8,19) if field=='anchor_date' else 'foreign')
    manager=StrategyOneManagementRunner(runtime=runtime,evidence=Evidence(),tick_for_ticker=lambda _: .01)
    with pytest.raises(ValueError,match='actual manager/runtime'):
        native.bind_prepared_structural_rejection_manager(manager,source)


def test_copied_or_mutated_prepared_source_is_not_authority(monkeypatch):
    source,_=prepared_source(monkeypatch)
    with pytest.raises(ValueError,match='Unissued'):
        native.require_prepared_structural_rejection_source(replace(source))
    object.__setattr__(source,'run_id','foreign')
    with pytest.raises(ValueError,match='binding changed'):
        native.require_prepared_structural_rejection_source(source)


@pytest.mark.parametrize('number',(1,42,57))
def test_genuine_legacy_contract_is_unselected(number):
    assert native_structural_rejection_declaration(numbered_fixed_strategy(number)) is None


@pytest.mark.parametrize('rules,inputs',(((RULE,),()),((),(SOURCE_INPUT,)),((RULE,),(SOURCE_INPUT,))))
def test_markers_without_typed_property_fail_closed(rules,inputs):
    with pytest.raises(ValueError,match='typed policy'):
        native_structural_rejection_declaration(SimpleNamespace(release=SimpleNamespace(rule_set_contracts=rules,input_contracts=inputs)))


def test_actual_manager_first_held_cross_rejections_pending_capture(monkeypatch):
    manager,owner,calls=fixture(monkeypatch); start(manager)
    assert owner._states[KEY].first_held_boundary_ms==1000
    assert owner._states[KEY].last_decision_boundary_ms==0
    async def run():
        for clock in (10000,15000,20000,25000):
            await manager.on_management(financial(),quote(owner,clock),clock)
    asyncio.run(run())
    requests=manager.structural_rejection_requests(boundary_ms=25000)
    assert len(requests)==1 and requests[0].witness.cross_prior.boundary_ms==10000
    assert requests[0].witness.cross.boundary_ms==15000
    assert requests[0].witness.resistance.price_int==106000
    assert manager.runtime.calls==['entry']  # No unfenced exit or order.
    capture=manager.capture_state(boundary_ms=25000)
    assert type(capture) is StructuralRejectionManagementState
    assert capture.structural_rejection_states[0][2]=='processed'
    assert calls==['market','seeds','intervals','bars','entry_authority']
    with pytest.raises(ValueError,match='fenced'):
        asyncio.run(manager.on_management(financial(),{},25100))
    owner.complete_requests(requests,boundary_ms=25000)
    asyncio.run(manager.on_management(financial(0),{},25100))
    assert owner.capture(boundary_ms=25100)==()


@pytest.mark.parametrize('field',('lookup','origin_us','_seed_units','_intervals','_coverage','_geometry_index','_valid_seconds','_index_frames'))
def test_issued_owner_source_substitution_rejected(monkeypatch,field):
    manager,owner,_=fixture(monkeypatch)
    value=getattr(owner,field)
    setattr(owner,field,owner.origin_us+1 if field=='origin_us' else
        MappingProxyType(dict(value)) if isinstance(value,MappingProxyType) else object())
    with pytest.raises(ValueError,match='binding changed'):
        native.require_native_structural_rejection_owner(owner)


def test_seed_inner_cache_is_immutable(monkeypatch):
    _,owner,_=fixture(monkeypatch)
    with pytest.raises(TypeError): owner._seed_units['AAA']['level_count']=0


def test_public_unissued_owner_cannot_bind(monkeypatch):
    manager,owner,_=fixture(monkeypatch)
    copied=native.NativeStructuralRejectionManager(manager,owner.market,owner.seeds,owner.intervals,
        owner.price_authority,owner.declaration,owner.lookup)
    with pytest.raises(ValueError,match='Unissued'):
        native.require_native_structural_rejection_owner(copied)


def test_quote_actual_time_stale_and_foreign_clock(monkeypatch):
    manager,owner,_=fixture(monkeypatch); start(manager)
    owner.begin_boundary(financial(),10000)
    row=quote(owner,10000); row[100]['boundary_ms']=10100
    with pytest.raises(ValueError,match='scheduler'):
        owner.observe(financial(),row,10000)
    row=quote(owner,10000); row[100]['quote_timestamp_us']+=1
    with pytest.raises(ValueError,match='future quote'):
        owner.observe(financial(),row,10000)


def test_inherited_liquidation_preempts_new_reducer_with_explicit_gap(monkeypatch):
    manager,owner,_=fixture(monkeypatch); start(manager)
    monkeypatch.setattr(Contract,'liquidation_due',lambda self,boundary:True,raising=False)
    asyncio.run(manager.on_management(financial(),quote(owner,10000),10000))
    assert owner._states[KEY].last_decision_boundary_ms==0
    assert manager.runtime.calls==['entry','liquidation']
    assert manager.capture_state(boundary_ms=10000).structural_rejection_states[0][2]=='inherited_priority_or_initial'


def test_remaining_native_checkpoint_route_is_explicitly_closed(monkeypatch):
    manager,owner,_=fixture(monkeypatch); start(manager)
    image=manager.capture_state(boundary_ms=1000)
    with pytest.raises(ValueError,match='verified native checkpoint'):
        manager.restore_state(image)


@pytest.mark.parametrize('lower,upper',((3.793259828906621,3.827044168285545),
    (1.0000000000000002,1.0000000000000004),(1.0000999999999998,1.0001000000000002)))
def test_declared_raw_float64_midpoint_and_outward_envelope(lower,upper):
    from math import floor,ceil
    declaration=NativeStructuralRejectionDeclaration()
    assert declaration.geometry_ints(lower,upper)==(floor(lower*10000),ceil(upper*10000),floor((lower+upper)/2*10000))


@pytest.mark.parametrize('lower,upper',((float('nan'),1.),(1.,float('inf')),(-1.,1.),(0.,1.),
    (2.,1.),(1e308,1e308),(2e15,2e15),(1e-10,1e-10),(1,2.)),
    ids=('nan','inf','negative','zero','inverted','float-overflow','uint64-overflow','sub-grid','integer-edge'))
def test_declared_geometry_malformed_or_unrepresentable_rejected(lower,upper):
    with pytest.raises(ValueError): NativeStructuralRejectionDeclaration().geometry_ints(lower,upper)


def test_real_off_lattice_source_edges_are_accepted_preserved_and_indexed_once(monkeypatch):
    origin=round(market_day_boundary(DAY,0).timestamp()*1000)
    raw=V7LevelInterval('raw-v7',1,1000,50000,10.593259828906621,10.627044168285545,
        'resistance','',origin+1000,False)
    manager,owner,calls=fixture(monkeypatch,levels=(raw,))
    start(manager)
    async def run():
        for clock in (10000,15000):
            await manager.on_management(financial(),quote(owner,clock),clock)
    asyncio.run(run())
    geometry=owner._geometries[KEY]
    assert geometry['interval']['lower']==raw.lower and geometry['interval']['upper']==raw.upper
    assert owner._states[KEY].resistance.price_int==NativeStructuralRejectionDeclaration().geometry_ints(raw.lower,raw.upper)[2]
    assert calls==['market','seeds','intervals','bars','entry_authority']


def test_integer_envelope_does_not_relax_original_entry_price_lattice():
    with pytest.raises(ValueError,match='lattice'): native._price(10.593259828906621)
