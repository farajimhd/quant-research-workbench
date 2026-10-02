"""Prepared manager/controller path; native cold readers and broker mocked."""
import asyncio
from dataclasses import replace
from datetime import date, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import polars as pl
import pytest

from src.backend.backtest_strategy_liquidity_fade import CompiledLiquidityFadeLookup
from src.backend.backtest_market_data import market_day_boundary
from src.backend.backtest_strategy_one_management import LiquidityFadeCheckpointRequest
from src.backend.backtest_typed_publisher import TypedBacktestReceipt
from src.trading_runtime.strategy_one_management_evidence import StrategyOneManagementEvidence
from src.trading_runtime.strategy_one_management_snapshot import _project_manager_snapshot_scalar, ManagerSnapshotHead
from src.trading_runtime.strategy_one_broker_match_snapshot import BrokerMatchHead
from src.trading_runtime.strategy_liquidity_fade_checkpoint_reference import confirm_liquidity_fade_checkpoint_sources
from test_arte_liquidity_fade_failure_v4 import prepared_case, IDENTITY
from test_strategy_liquidity_fade_market_source import native_case
from test_profit_arming_engine_boundary import manager_fixture
from test_liquidity_fade_financial_checkpoint import case as broker_case


def manager_case():
    manager, _, _ = manager_fixture()
    witness, financial, state, _ = prepared_case()
    manager.contract = SimpleNamespace(strategy_number=35, allows_followthrough_failure_exit=True,
        liquidation_due=lambda _:False, allows_completed_30s_trailing=False, allows_target_escalation=False,
        allows_adds=False)
    manager.runtime.config.anchor_date = date(2026,8,10)
    manager.runtime._strategy_one_entry_intent = Mock(return_value=SimpleNamespace(intent_id=IDENTITY))
    manager.runtime.submit_followthrough_failure = AsyncMock()
    manager.runtime.submit_profit_giveback = AsyncMock()
    manager.runtime.submit_confirmed_ah_failure = AsyncMock()
    manager.runtime.submit_liquidity_fade_failure = AsyncMock()
    for family in ('submitted','positions','position_highs','first_held_boundaries'):
        setattr(manager,'_'+family,dict(getattr(state,family)))
    manager._profit_arm_financials.clear()
    key = state.submitted[0][0]
    manager._positions[key] = replace(manager._positions[key], boundary_ms=44_804_900)
    _, source, bars, _, _, args = native_case()
    frame = pl.DataFrame([dict(source_build_id=source['source_build_id'], session_date='2026-08-10',
        ticker=financial.ticker, source_attempt_id=source['source_bars_attempt_id'],
        boundary_ms=c.boundary_ms, trade_count=c.trade_count) for c in witness.candles])
    lookup = CompiledLiquidityFadeLookup(frame, plan=args['plan'], session_date=date(2026,8,10))
    manager.bind_liquidity_fade_lookup(lookup,args['plan'])
    def rows(boundary, *, completed=False, age=48):
        at = market_day_boundary(date(2026,8,10),boundary)
        manager.evidence.management_evidence = AsyncMock(return_value=StrategyOneManagementEvidence(
            financial.ticker,boundary,witness.bid,witness.ask,True,None,None,(),()))
        result={100:dict(price_valid=1,high_int=23200,quote_valid=1,
                         quote_timestamp_us=int(at.timestamp()*1_000_000)-age)}
        if completed:
            result[5000]=dict(boundary_ms=witness.completed_five_second_boundary_ms,price_valid=1,
                close_int=witness.completed_close_int,macd_line=witness.macd_line,macd_signal=witness.macd_signal)
        return result
    return manager,witness,financial,state,rows


def test_quote_only_confirmation_waits_for_checkpoint_and_retains_completed_signal():
    manager,witness,financial,_,rows = manager_case()
    asyncio.run(manager.on_management(financial,rows(44_805_000,completed=True,age=1_400_000),44_805_000))
    assert manager.liquidity_fade_requests(boundary_ms=44_805_000)==()
    asyncio.run(manager.on_management(financial,rows(witness.boundary_ms),witness.boundary_ms))
    requests=manager.liquidity_fade_requests(boundary_ms=witness.boundary_ms)
    assert len(requests)==1 and requests[0].witness.completed_five_second_boundary_ms==44_805_000
    assert requests[0].witness.boundary_ms==witness.boundary_ms
    manager.runtime.submit_liquidity_fade_failure.assert_not_awaited()
    with pytest.raises(RuntimeError,match='not fenced'):
        manager.liquidity_fade_requests(boundary_ms=witness.boundary_ms+100)
    manager.complete_liquidity_fade_requests(requests,boundary_ms=witness.boundary_ms)
    assert manager.liquidity_fade_requests(boundary_ms=witness.boundary_ms)==()


@pytest.mark.parametrize('case',['missing','stale','pending','forming'])
def test_missing_stale_pending_or_forming_signal_never_queues_exit(case):
    manager,witness,financial,_,rows=manager_case()
    if case!='missing':
        initial=rows(44_805_000,completed=True,age=1_400_000)
        if case=='forming': initial[5000]['boundary_ms']=44_810_000
        if case=='forming':
            with pytest.raises(ValueError,match='forming'):
                asyncio.run(manager.on_management(financial,initial,44_805_000))
            return
        asyncio.run(manager.on_management(financial,initial,44_805_000))
    if case=='pending':financial=replace(financial,pending_exit=True)
    asyncio.run(manager.on_management(financial,rows(witness.boundary_ms,age=1_000_001 if case=='stale' else 48),witness.boundary_ms))
    assert manager.liquidity_fade_requests(boundary_ms=witness.boundary_ms)==()


def reference_case(monkeypatch):
    broker=broker_case(monkeypatch)
    witness,financial,state,row=prepared_case()
    manager=_project_manager_snapshot_scalar(run_id='run',session_date=date(2026,8,10),checkpoint_sequence=64,state=state)
    manager_head=ManagerSnapshotHead('run',64,'prior',manager.snapshot['content_hash'],1)
    broker_head=BrokerMatchHead('run',64,'prior',broker[5].snapshot['content_hash'],1)
    mkeeper,bkeeper=SimpleNamespace(read_head=lambda **_:manager_head),SimpleNamespace(read_head=lambda **_:broker_head)
    monkeypatch.setattr('src.trading_runtime.strategy_one_management_snapshot.load_attested_manager_snapshot',lambda *a,**k:state)
    monkeypatch.setattr('src.trading_runtime.strategy_one_management_snapshot.load_unattested_manager_snapshot_rows',lambda *a,**k:manager)
    monkeypatch.setattr('src.trading_runtime.strategy_one_broker_match_snapshot.load_attested_broker_match_snapshot',lambda *a,**k:broker[5])
    _,source,*_=native_case()
    request=LiquidityFadeCheckpointRequest(witness,financial,IDENTITY,source)
    return request,TypedBacktestReceipt(64,'prior','cursor'),mkeeper,bkeeper,manager,broker


def test_native_receipt_selects_both_exact_snapshot_references(monkeypatch):
    request,receipt,mkeeper,bkeeper,manager,broker=reference_case(monkeypatch)
    refs,=confirm_liquidity_fade_checkpoint_sources(None,mkeeper,bkeeper,(request,),receipt,run_id='run',first_price_source='pinned')
    assert refs['source_manager_snapshot_id']==manager.snapshot['snapshot_id']
    assert refs['source_broker_snapshot_hash']==broker[5].snapshot['content_hash']
    assert refs['source_manager_checkpoint_sequence']==64
    with pytest.raises(TypeError):refs['source_manager_checkpoint_sequence']=65


@pytest.mark.parametrize('case',['receipt','manager_hash','broker_hash','first_held','duplicate','head_race'])
def test_changed_native_reference_or_position_rejects_all_requests(monkeypatch,case):
    request,receipt,mkeeper,bkeeper,manager,broker=reference_case(monkeypatch)
    requests=(request,)
    if case=='receipt':receipt=replace(receipt,last_sequence=63)
    elif case=='manager_hash':manager.snapshot['content_hash']='f'*64
    elif case=='broker_hash':broker[5].snapshot['content_hash']='f'*64
    elif case=='first_held':request=replace(request,witness=replace(request.witness,first_held_boundary_ms=request.witness.first_held_boundary_ms+100));requests=(request,)
    elif case=='duplicate':requests*=2
    else:
        head=mkeeper.read_head();calls=iter((head,replace(head,keeper_version=2)))
        mkeeper.read_head=lambda **_:next(calls)
    with pytest.raises(ValueError):
        confirm_liquidity_fade_checkpoint_sources(None,mkeeper,bkeeper,requests,receipt,run_id='run',first_price_source='pinned')


@pytest.mark.parametrize('failure', [None, 'financial', 'checkpoint', 'confirmation'])
def test_controller_fences_and_confirms_before_submitting(monkeypatch, failure):
    from src.backend.replay_run_service import ReplayRunController, RunMode
    request, receipt, _, _, _, _ = reference_case(monkeypatch)
    calls = []
    controller = object.__new__(ReplayRunController)
    controller.definition = SimpleNamespace(mode=RunMode.BACKTEST)
    controller.run_id = 'run'
    controller._source_cursor = {'boundary_ms': request.witness.boundary_ms}
    controller._fixed_keeper_session = object()
    controller._strategy_one_manager = SimpleNamespace(
        contract=SimpleNamespace(strategy_number=35),
        liquidity_fade_requests=lambda **_: (request,),
        complete_liquidity_fade_requests=lambda *a, **k: calls.append('clear'))
    controller._journal_publisher = SimpleNamespace(
        writer=SimpleNamespace(journal_profile='backtest_v4'), _first_price_source='pinned')
    financial = request.financial
    assignment = SimpleNamespace(account_id=financial.account_id,
        assignment_id=financial.assignment_id, ticker=financial.ticker)
    async def read_financial(*args):
        calls.append('financial')
        return replace(financial, pending_exit=True) if failure == 'financial' else financial
    async def checkpoint(*args):
        calls.append('checkpoint')
        if failure == 'checkpoint': raise RuntimeError('checkpoint rejected')
        return receipt
    source = {'confirmed': True}
    def confirm(*args, **kwargs):
        calls.append('confirmation')
        assert args[3] == (request,) and args[4] == receipt
        if failure == 'confirmation': raise RuntimeError('confirmation rejected')
        return (source,)
    async def submit(*args):
        calls.append('submit')
        assert args == (financial, request.witness, IDENTITY, source)
    controller._runtime = SimpleNamespace(strategy=SimpleNamespace(assignments=lambda: (assignment,)),
        broker=object(), order_manager=object(), submit_liquidity_fade_failure=AsyncMock(side_effect=submit))
    controller._save_restart_checkpoint_responsive = checkpoint
    monkeypatch.setattr('src.backend.backtest_strategy_one_financial.read_strategy_one_financial_view', read_financial)
    monkeypatch.setattr('src.trading_runtime.arte_journal_writer.backtest_v4_operator_client_from_env',
        lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr('src.trading_runtime.strategy_one_management_snapshot.ManagedManagerSnapshotHeadReader', lambda _: object())
    monkeypatch.setattr('src.trading_runtime.strategy_one_broker_match_snapshot.ManagedBrokerMatchHeadReader', lambda _: object())
    monkeypatch.setattr('src.trading_runtime.strategy_liquidity_fade_checkpoint_reference.confirm_liquidity_fade_checkpoint_sources', confirm)
    run = controller._confirm_liquidity_fade_checkpoint((request,), event_time=object())
    if failure is None:
        asyncio.run(run)
        assert calls == ['financial', 'checkpoint', 'confirmation', 'submit', 'clear']
    else:
        with pytest.raises(RuntimeError): asyncio.run(run)
        controller._runtime.submit_liquidity_fade_failure.assert_not_awaited()
        assert 'clear' not in calls
