"""Native 100ms cancellation confirmation before a global financial capture."""
import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.trading_runtime.arte_oms_actor_restore import capture_typed_oms_actor_image, verify_typed_oms_broker_open_orders
from src.trading_runtime.domain import InstrumentContract, TradingMode
from src.trading_runtime.ibkr_schema import OPEN_ORDER_STATUSES, OrderStatus
from src.trading_runtime.journal import TradingJournal
from src.trading_runtime.order_management import OrderManagementEngine, OrderManagementState
from src.trading_runtime.oms_assignment_observation import observe_assignment_orders
from src.trading_runtime.risk import RiskAuthority
from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter, SimulationConfig
from src.trading_runtime.strategy_one_broker_match_snapshot import project_broker_match_snapshot
from src.trading_runtime.strategy_orders import RuntimeIbkrStrategyOrderPlanner
from tests.test_order_management import intent, portfolio_approved
from tests.test_simulated_broker_liquidity_bar import bar
from src.backend.backtest_market_data import market_day_boundary
from src.backend.backtest_program_clock import reconcile_program_clock_order_observations

NOW = datetime(2026,8,3,14,tzinfo=timezone.utc)


@pytest.mark.parametrize('confirmation', (True, False))
def test_other_assignment_capture_observes_cancellations_without_matching_or_fake_terminal(tmp_path, confirmation):
    async def run():
        broker = SimulatedBrokerAdapter(['DU1'],mode=TradingMode.BACKTEST,initial_time=NOW,
            fixed_bar_mode=True,config=SimulationConfig(liquidity_resolution_ms=100))
        await broker.initialize()
        risk = RiskAuthority(); await risk.prime(broker,['DU1'])
        planner = RuntimeIbkrStrategyOrderPlanner({ticker: InstrumentContract(ticker,conid,ticker,'STK','USD')
            for ticker,conid in (('TEST',123),('OTHER',456))},strategy_id='test-program',strategy_revision=99,run_id='run-1')
        journal = TradingJournal(tmp_path/'orders.sqlite3')
        manager = OrderManagementEngine(broker=broker,planner=lambda request,account,event:
            planner.plan(intent=request,account_id=account,event=event),risk=risk,journal=journal,
            run_id='run-1',strategy_id='test-program',strategy_revision=99,causal_execution_clock=True)
        try:
            for ticker in ('TEST','OTHER'):
                for index in range(2):
                    entry = replace(intent(quantity=10),intent_id=f'{ticker}-entry-{index}',ticker=ticker,event_time=NOW,
                        profit_target_price=None,metadata={**intent().metadata,'assignment_id':ticker,
                            'quote_observed_at':NOW.isoformat()})
                    await manager.submit_intent(portfolio_approved(journal,entry),account_id='DU1',event=None)
            at = NOW + timedelta(milliseconds=100)
            for ticker in ('TEST','OTHER'):
                row = bar(at,ask_size=1000,bid_size=1000,execution_volume=1000);row['ticker']=ticker
                await broker.on_liquidity_bar(row,at=at)
                await manager.reconcile()
            def image():
                return project_broker_match_snapshot(run_id='run-1',session_date=NOW.date(),
                    checkpoint_sequence=journal.latest_sequence('run-1'),
                    boundary_ms=int((at-market_day_boundary(NOW.date(),0)).total_seconds()*1000),
                    state=broker.broker_match_snapshot_state())
            assert verify_typed_oms_broker_open_orders(capture_typed_oms_actor_image(manager),image()) == 4
            source_groups = [group for group in manager._groups.values()
                if group.intent.ticker == 'TEST' and group.intent.action == 'enter_long']
            if not confirmation:
                async def pending_cancel(account_id,order_id):
                    broker._orders[order_id].status = OrderStatus.PENDING_CANCEL
                    return {'order_id':order_id,'status':'PendingCancel'}
                broker.cancel_order = pending_cancel
            exit_intent = replace(intent(action='exit',quantity=20),intent_id='terminal-TEST',
                event_time=at,profit_target_price=None,metadata={**intent().metadata,
                    'assignment_id':'TEST','position_quantity':20.,'position_side':'long',
                    'quote_observed_at':at.isoformat()})
            if confirmation:
                await manager.submit_intent(portfolio_approved(journal,exit_intent),account_id='DU1',event=None)
            else:
                for group in source_groups: group.protection_delegated = True
                await manager.cancel_strategy_protection(account_id='DU1',ticker='TEST',
                    client_id_prefix=manager._protective_order_prefix(),event_time=at)
            assert all(group.state == OrderManagementState.CANCEL_PENDING for group in source_groups)
            assert manager.has_pending_cancellations
            controller = SimpleNamespace(_runtime=SimpleNamespace(order_manager=manager),
                program_order_observation=lambda *,assignment_id,at: observe_assignment_orders(manager,
                    account_id='DU1',assignment_id=assignment_id,ticker=assignment_id,at=at))
            assert not controller.program_order_observation(assignment_id='OTHER',at=at).unobserved_order_ids
            if confirmation:
                with pytest.raises(RuntimeError,match='disagree on open order identities'):
                    verify_typed_oms_broker_open_orders(capture_typed_oms_actor_image(manager),image())
            before = tuple(broker._executions)
            await reconcile_program_clock_order_observations(controller,assignment_id='OTHER',at=at)
            assert verify_typed_oms_broker_open_orders(capture_typed_oms_actor_image(manager),image()) == (3 if confirmation else 4)
            assert manager.has_pending_cancellations != confirmation
            assert tuple(broker._executions) == before
            for group in source_groups:
                stops = {identity for identity,role in group.broker_order_roles.items() if role == 'protective_stop'}
                assert bool(stops & group.terminal_broker_order_ids) == confirmation
                if not confirmation: assert group.state == OrderManagementState.CANCEL_PENDING
            if confirmation:
                async def unexpected_read():
                    raise AssertionError('Unchanged clock performed an unnecessary broker reconciliation')
                broker.live_orders = unexpected_read
                await reconcile_program_clock_order_observations(controller,assignment_id='OTHER',at=at)
            assert sum(row.position for row in await broker.positions('DU1') if row.conid == 123) == 20
        finally:
            await manager.close();journal.close()
    asyncio.run(run())
