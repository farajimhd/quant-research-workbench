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
from test_strategy_forty_two_management import prepared_manager
from test_confirmed_original_risk_source import plan


@pytest.mark.parametrize('inherited',[False,True])
@pytest.mark.parametrize('prior_ok',[False,True])
def test_actual_manager_consecutive_extension_and_inherited_priority(monkeypatch,inherited,prior_ok):
    manager,w,financial,rows=prepared_manager(68)
    manager.contract=numbered_fixed_strategy(68)
    key=financial.account_id,financial.assignment_id,financial.ticker
    source=manager._submitted[key];at=w.completed_five_second_boundary_ms+120000
    manager._first_held_boundaries[key]=source.boundary_ms+100
    price=int((source.reference_ask+source.initial_stop)/2*10000)-1 if inherited else int(
        (3*source.reference_ask+source.initial_stop)/4*10000)-1
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
    if not inherited and not prior_ok:
        manager.runtime.submit_followthrough_failure.assert_not_awaited()
        return
    manager.runtime.submit_followthrough_failure.assert_not_awaited()
    requests=manager.original_risk_requests(boundary_ms=at)
    assert len(requests)==1
    request=requests[0]
    diagnostic=request.diagnostic
    assert diagnostic.checkpoint is None
    assert request.financial==financial
    assert diagnostic.semantic_rule==(INHERITED_ORIGINAL_RISK_RULE if inherited else CONFIRMED_ORIGINAL_RISK_RULE)
    assert diagnostic.prior is None if inherited else diagnostic.prior==lookup.pair_at(financial.ticker,at)[0]
    validate_witness(request.witness,strategy_number=68,diagnostic=diagnostic)
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
