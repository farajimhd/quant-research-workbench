"""Actual manager; completed producer plan and broker facade are synthetic."""
import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock

import polars as pl
import pytest

from src.backend.backtest_confirmed_original_risk_source import CompiledCompletedRiskLookup, REQUIRED
from src.trading_runtime.numbered_fixed_strategy import numbered_fixed_strategy
from src.trading_runtime.confirmed_original_risk_failure import (
    CONFIRMED_ORIGINAL_RISK_RULE, INHERITED_ORIGINAL_RISK_RULE,
)
from src.trading_runtime.strategy_followthrough_exit import validate_witness
from src.trading_runtime.premarket_confirmed_original_risk import PREMARKET_CONFIRMED_RISK_RULE
from test_strategy_forty_two_management import prepared_manager
from test_confirmed_original_risk_source import plan


@pytest.mark.parametrize('prior_ok',[False,True])
@pytest.mark.parametrize('stage,threshold_edge',[('pm',False),('pm',True),('late',False),('ah',False)])
def test_actual_manager_replacement_checkpoint_and_pending_cold(monkeypatch,prior_ok,stage,threshold_edge):
    inherited=False
    manager,w,financial,rows=prepared_manager(74)
    manager.contract=numbered_fixed_strategy(74)
    key=financial.account_id,financial.assignment_id,financial.ticker
    source=manager._submitted[key]
    if stage!='ah':source=replace(source,boundary_ms=41000,episode_start_ms=30000,bos_break_boundary_ms=40000)
    if threshold_edge:source=replace(source,reference_ask=1.0003,initial_stop=.9983,initial_target=1.01)
    manager._submitted[key]=source
    at=100000 if stage=='pm' else 140000 if stage=='late' else w.completed_five_second_boundary_ms+120000
    held=at-(20000 if stage=='pm' else 88000)
    manager._first_held_boundaries[key]=held
    manager._positions[key]=replace(manager._positions[key],boundary_ms=held-100)
    price=int((source.reference_ask+source.initial_stop)/2*10000)-1 if inherited else int(
        (3*source.reference_ask+source.initial_stop)/4*10000)-1
    if threshold_edge:price=9998
    line,signal=(-.02,-.01) if inherited else (.01,.02)
    p=plan();session=manager.runtime.config.anchor_date.isoformat()
    p=replace(p,sessions=(session,),tickers=(financial.ticker,),units=tuple(
        replace(u,session_date=session,ticker=financial.ticker) for u in p.units))
    frame=pl.DataFrame(dict(source_build_id=[p.build_id]*2,source_market_plan_token=[p.token]*2,
        source_bars_attempt_id=[p.units[0].attempt_id]*2,
        source_indicators_attempt_id=[p.units[1].attempt_id]*2,
        source_liquidity_attempt_id=[p.units[2].attempt_id]*2,
        session_date=[session]*2,ticker=[financial.ticker]*2,boundary_ms=[at-5000,at],
        close_int=[price,price],price_valid=[prior_ok,True],
        macd_line=[line,line],macd_signal=[signal,signal]))
    lookup=CompiledCompletedRiskLookup(frame.select(REQUIRED),plan=p,session_date=manager.runtime.config.anchor_date)
    manager.bind_completed_risk_lookup(lookup,p)
    current=rows(at,completed=True,age=48)
    current[5000].update(boundary_ms=at,close_int=price,macd_line=line,macd_signal=signal)
    evidence=asyncio.run(manager.evidence.management_evidence(financial.ticker,current,boundary_ms=at))
    manager.evidence.management_evidence=AsyncMock(return_value=replace(evidence,bid=price/10000,ask=price/10000+.01))
    if inherited:
        import src.trading_runtime.confirmed_original_risk_failure as extension
        def forbidden(*args,**kwargs):raise AssertionError('Inherited exit must precede extension')
        monkeypatch.setattr(extension,'confirmed_original_risk_failure',forbidden)
    asyncio.run(manager.on_management(financial,current,at))
    if not prior_ok or threshold_edge:
        manager.runtime.submit_followthrough_failure.assert_not_awaited()
        assert manager.original_risk_requests(boundary_ms=at)==()
        assert key in manager._positions  # missing pair does not abandon protection ownership
        assert manager._positions[key].boundary_ms==at
        return
    manager.runtime.submit_followthrough_failure.assert_not_awaited()
    requests=manager.original_risk_requests(boundary_ms=at)
    assert len(requests)==1
    request=requests[0]
    diagnostic=request.diagnostic
    assert diagnostic.checkpoint is None
    assert request.financial==financial
    assert diagnostic.semantic_rule==(PREMARKET_CONFIRMED_RISK_RULE if stage=='pm' else CONFIRMED_ORIGINAL_RISK_RULE)
    assert diagnostic.prior is None if inherited else diagnostic.prior==lookup.pair_at(financial.ticker,at)[0]
    validate_witness(request.witness,strategy_number=74,diagnostic=diagnostic)
    assert request.witness.reference_ask==source.reference_ask
    assert request.witness.initial_stop==source.initial_stop
    from src.trading_runtime.strategy_one_management_snapshot import (
        _project_manager_snapshot_scalar,restore_manager_snapshot,OriginalRiskManagerSnapshotRows,
    )
    captured=manager.capture_state(boundary_ms=at)
    saved=_project_manager_snapshot_scalar(run_id='synthetic-checkpoint',
        session_date=manager.runtime.config.anchor_date,checkpoint_sequence=9,state=captured)
    assert type(saved) is OriginalRiskManagerSnapshotRows
    assert saved.snapshot['original_risk_pending_count']==1
    restored=restore_manager_snapshot(saved)
    assert restored.original_risk_requests[0].diagnostic==diagnostic
    assert restored.original_risk_requests[0].source_entry_intent_id==request.source_entry_intent_id
    with pytest.raises(ValueError,match='complete pending'):
        restore_manager_snapshot(replace(saved,original_risk_requests=()))
    with pytest.raises(ValueError,match='repeats held identity'):
        restore_manager_snapshot(replace(saved,original_risk_requests=(
            saved.original_risk_requests[0],saved.original_risk_requests[0])))
    with pytest.raises(RuntimeError,match='before advancing boundary'):
        manager.original_risk_requests(boundary_ms=at+100)
    with pytest.raises(ValueError):
        manager.complete_original_risk_requests((),boundary_ms=at)
    assert manager.original_risk_requests(boundary_ms=at)==requests
    manager.complete_original_risk_requests(requests,boundary_ms=at)
    assert manager.original_risk_requests(boundary_ms=at)==()
    manager.runtime.submit_profit_giveback.assert_not_awaited()
    manager.runtime.submit_confirmed_ah_failure.assert_not_awaited()

@pytest.mark.parametrize('other_exit',['profit','liquidity'])
def test_missing_confirmation_pair_preserves_actual_inherited_exit_priority(other_exit):
    from src.trading_runtime.strategy_profit_giveback_arm import ProfitArmCandidate
    from test_profit_arming_engine_boundary import reference
    from test_strategy_liquidity_fade_market_source import native_case
    from src.backend.backtest_strategy_liquidity_fade import CompiledLiquidityFadeLookup
    manager,_,financial,rows=prepared_manager(74)
    manager.contract=numbered_fixed_strategy(74)
    key=financial.account_id,financial.assignment_id,financial.ticker
    source=replace(manager._submitted[key],boundary_ms=41000,episode_start_ms=30000,bos_break_boundary_ms=40000)
    manager._submitted[key]=source;at=100000;held=80000
    manager._first_held_boundaries[key]=held
    manager._positions[key]=replace(manager._positions[key],boundary_ms=99900)
    p=plan();session=manager.runtime.config.anchor_date.isoformat()
    p=replace(p,sessions=(session,),tickers=(financial.ticker,),units=tuple(replace(u,session_date=session,ticker=financial.ticker) for u in p.units))
    price=int((source.reference_ask+source.initial_stop)/2*10000)-1
    # Only newest is certified: missing prior cannot authorize replacement.
    frame=pl.DataFrame(dict(source_build_id=[p.build_id],source_market_plan_token=[p.token],source_bars_attempt_id=[p.units[0].attempt_id],source_indicators_attempt_id=[p.units[1].attempt_id],source_liquidity_attempt_id=[p.units[2].attempt_id],session_date=[session],ticker=[financial.ticker],boundary_ms=[at],close_int=[price],price_valid=[True],macd_line=[.01],macd_signal=[.02]))
    manager.bind_completed_risk_lookup(CompiledCompletedRiskLookup(frame.select(REQUIRED),plan=p,session_date=manager.runtime.config.anchor_date),p)
    current=rows(at,completed=True,age=48)
    current[5000].update(boundary_ms=at,close_int=price,macd_line=.01,macd_signal=.02)
    evidence=asyncio.run(manager.evidence.management_evidence(financial.ticker,current,boundary_ms=at))
    manager.evidence.management_evidence=AsyncMock(return_value=replace(evidence,bid=price/10000,ask=price/10000+.01))
    if other_exit=='profit':
        high=int((2*source.reference_ask-source.initial_stop)*10000)+1
        candidate=ProfitArmCandidate(financial.account_id,financial.assignment_id,financial.ticker,90000,held,source.reference_ask,source.initial_stop,high)
        manager._profit_arm_references[key]=reference(candidate)
    else:
        _,observation,_,_,_,args=native_case()
        activity=pl.DataFrame([dict(source_build_id=observation['source_build_id'],session_date=session,ticker=financial.ticker,source_attempt_id=observation['source_bars_attempt_id'],boundary_ms=at-offset,trade_count=count) for offset,count in zip((15000,10000,5000,0),(57,18,10,5))])
        manager._liquidity_lookup=None
        manager.bind_liquidity_fade_lookup(CompiledLiquidityFadeLookup(activity,plan=args['plan'],session_date=manager.runtime.config.anchor_date,strategy_number=74),args['plan'])
    asyncio.run(manager.on_management(financial,current,at))
    assert manager.original_risk_requests(boundary_ms=at)==()
    manager.runtime.submit_followthrough_failure.assert_not_awaited()
    if other_exit=='profit':
        manager.runtime.submit_profit_giveback.assert_awaited_once()
        assert manager.liquidity_fade_requests(boundary_ms=at)==()
    else:
        assert len(manager.liquidity_fade_requests(boundary_ms=at))==1
        manager.runtime.submit_profit_giveback.assert_not_awaited()
