"""Prepared management route; installed release and native writers remain gated."""
import asyncio
from dataclasses import replace

import polars as pl
import pytest

from src.backend.backtest_strategy_liquidity_fade import CompiledLiquidityFadeLookup
from src.trading_runtime.strategy_half_risk_liquidity_fade import HalfRiskLiquidityFadeFailure
from src.trading_runtime.strategy_liquidity_fade_failure import LiquidityFadeFailure
from test_liquidity_fade_manager_checkpoint_route import manager_case
from test_strategy_liquidity_fade_market_source import native_case


def prepared_manager(number=41, counts=(57,18,20,10)):
    manager,witness,financial,state,rows=manager_case()
    manager.contract.strategy_number=number
    key,source=state.submitted[0]
    manager._submitted[key]=replace(source,strategy_number=number)
    _,observation,_,_,_,args=native_case()
    frame=pl.DataFrame([dict(source_build_id=observation['source_build_id'],
        session_date='2026-08-10',ticker=financial.ticker,
        source_attempt_id=observation['source_bars_attempt_id'],
        boundary_ms=c.boundary_ms,trade_count=count)
        for c,count in zip(witness.candles,counts)])
    manager._liquidity_lookup=None
    lookup=CompiledLiquidityFadeLookup(frame,plan=args['plan'],
        session_date=manager.runtime.config.anchor_date,strategy_number=number)
    manager.bind_liquidity_fade_lookup(lookup,args['plan'])
    return manager,witness,financial,rows


def observe(manager,witness,financial,rows,*,close=23100,age=48):
    initial=rows(witness.completed_five_second_boundary_ms,completed=True,age=1_400_000)
    initial[5000]['close_int']=close
    asyncio.run(manager.on_management(financial,initial,witness.completed_five_second_boundary_ms))
    asyncio.run(manager.on_management(financial,rows(witness.boundary_ms,age=age),witness.boundary_ms))
    return manager.liquidity_fade_requests(boundary_ms=witness.boundary_ms)


def test_additional_exit_is_checkpoint_request_and_does_not_submit_an_order():
    manager,witness,financial,rows=prepared_manager()
    request,=observe(manager,witness,financial,rows)
    assert type(request.witness) is HalfRiskLiquidityFadeFailure
    assert request.witness.first_held_boundary_ms==manager._first_held_boundaries[
        (financial.account_id,financial.assignment_id,financial.ticker)]
    assert tuple(c.trade_count for c in request.witness.candles)==(57,18,20,10)
    manager.runtime.submit_liquidity_fade_failure.assert_not_awaited()
    manager.runtime.submit_strategy_one_protection.assert_not_awaited()


@pytest.mark.parametrize('number',[35,36,37,38])
def test_older_manager_does_not_take_additional_half_rate_exit(number):
    manager,witness,financial,rows=prepared_manager(number)
    assert observe(manager,witness,financial,rows)==()


def test_inherited_quarter_rate_witness_retains_priority():
    manager,witness,financial,rows=prepared_manager(counts=(57,18,10,5))
    request,=observe(manager,witness,financial,rows)
    assert type(request.witness) is LiquidityFadeFailure


@pytest.mark.parametrize('change',['above_half_risk','stale_quote','pending_exit','late_first_held'])
def test_incomplete_failure_does_not_queue_new_exit(change):
    manager,witness,financial,rows=prepared_manager()
    close=23100;age=48
    if change=='above_half_risk':close=23200
    elif change=='stale_quote':age=1_000_001
    elif change=='pending_exit':financial=replace(financial,pending_exit=True)
    else:
        key=(financial.account_id,financial.assignment_id,financial.ticker)
        manager._first_held_boundaries[key]=witness.candles[0].boundary_ms
    assert observe(manager,witness,financial,rows,close=close,age=age)==()


@pytest.mark.parametrize('number',[38,39,41])
def test_half_rate_cache_cannot_bind_to_wrong_policy(number):
    manager,_,_,_=prepared_manager(number)
    cache=manager._liquidity_lookup
    manager._liquidity_lookup=None
    manager.contract.strategy_number=41 if number==38 else 38
    _,_,_,_,_,args=native_case()
    with pytest.raises(ValueError,match='exact prepared session lookup'):
        manager.bind_liquidity_fade_lookup(cache,args['plan'])
