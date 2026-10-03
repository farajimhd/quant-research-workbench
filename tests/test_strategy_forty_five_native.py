import asyncio
import pytest
from datetime import date
from uuid import uuid4, uuid5, NAMESPACE_URL

from src.backend.backtest_strategy_forty_five_journal import StrategyFortyFiveJournal
from src.backend.backtest_strategy_forty_five_native import StrategyFortyFiveNativePort
from src.backend.backtest_strategy_forty_five_publisher import StrategyFortyFivePublisher
from src.trading_runtime.domain import InstrumentContract, TradingMode
from src.trading_runtime.execution_policies import ExecutionMarketSnapshot
from src.trading_runtime.portfolio import PortfolioAccountProfile, PortfolioManagementEngine, PortfolioPolicy
from src.trading_runtime.runtime import RunConfig, RunMode, typed_run_config_payload
from src.trading_runtime.strategy_forty_five_runtime import StrategyFortyFiveRuntime as TradingRuntime
from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter, SimulationConfig
from src.trading_runtime.strategy_engine import StrategyAssignment, StrategyPermissions, AssignmentStatus
from src.trading_runtime.strategy_forty_five_coordinator import StrategyFortyFiveCoordinator
from src.trading_runtime.strategy_forty_five_rules import entry_intents, propose_batch
from src.trading_runtime.strategy_forty_five_runtime import AssignedStrategyFortyFive
from src.trading_runtime.strategy_forty_five_orders import StrategyFortyFiveOrderPlanner
from tests.test_backtest_typed_publisher import FakeWriter
from tests.test_strategy_forty_five_rules import facts


def test_fence_drains_callback_suffix_and_bounds_nonquiescent_writer():
    from types import SimpleNamespace
    async def run(changes):
        journal = SimpleNamespace(sequence=1)
        journal.latest_sequence = lambda _: journal.sequence
        class Publisher:
            calls = 0
            def enqueue_pending(self):
                self.target = journal.sequence
            async def await_fence(self):
                self.calls += 1
                if self.calls <= changes:
                    journal.sequence += 1
                return SimpleNamespace(last_sequence=self.target)
        publisher = Publisher()
        port = SimpleNamespace(publisher=publisher, runtime=SimpleNamespace(
            journal=journal, run_id="run"))
        if changes >= 32:
            with pytest.raises(RuntimeError, match="not fully fenced"):
                await StrategyFortyFiveNativePort._fence(port)
            assert publisher.calls == 32
        else:
            receipt = await StrategyFortyFiveNativePort._fence(port)
            assert receipt.last_sequence == journal.sequence
            assert publisher.calls == changes + 1
    asyncio.run(run(1))
    asyncio.run(run(32))


@pytest.mark.parametrize("invalidate", [False, True, "liquidity"])
def test_fenced_batch_uses_actual_native_portfolio_and_oms_for_fifteen_parents(invalidate):
    async def run():
        day = date(2026, 9, 3)
        batch = propose_batch(facts(), account_id="A", assignment_id="X",
            free_cash_after_reservations=10_000., already_submitted=False)
        at = entry_intents(batch, session_date=day)[0].event_time
        run_id = str(uuid4())
        journal = StrategyFortyFiveJournal(run_id=run_id)
        assignment = StrategyAssignment("X", "squeeze-grid-strategy", 45, "A", "TEST", 123,
            AssignmentStatus.WATCHING, StrategyPermissions(enter=True), {})
        broker = SimulatedBrokerAdapter(["A"], SimulationConfig(initial_cash=10_000.),
            mode=TradingMode.BACKTEST, initial_time=at, fixed_bar_mode=True)
        await broker.initialize()
        policy = PortfolioPolicy(maximum_position_fraction=1., maximum_ticker_fraction=1.,
            maximum_planned_risk_fraction=.5, maximum_open_risk_fraction=.5,
            allow_outside_rth=True, entry_fee_buffer_bps=0.)
        portfolio = PortfolioManagementEngine([PortfolioAccountProfile("cash", "A", "backtest",
            "simulated", policy)], journal=journal, run_id=run_id,
            strategy_id="squeeze-grid-strategy", strategy_revision=45, event_clock=lambda: at)
        planner = StrategyFortyFiveOrderPlanner({"TEST": InstrumentContract(
            "conid:123", 123, "TEST", "STK", "SMART", "USD")}, run_id=run_id)
        runtime = TradingRuntime(config=RunConfig(RunMode.BACKTEST, "squeeze-grid-strategy", 45,
            ("A",), day, run_id=run_id, safety_supervisor_enabled=False,
            write_progress_checkpoints=False), broker=broker,
            strategy=AssignedStrategyFortyFive([assignment]), journal=journal,
            portfolio=portfolio, intent_planner=planner)
        writer = FakeWriter()
        writer.run_id, writer.journal_profile = run_id, "backtest_v4"
        writer.submit_base_v4 = writer.submit
        writer.submit_compound_v4 = lambda unit, **_: writer.submit(unit.base)
        publisher = StrategyFortyFivePublisher(journal, writer, attempt_id=str(uuid4()),
            run_month=day.replace(day=1), expected_config=typed_run_config_payload(runtime.config))
        port = StrategyFortyFiveNativePort(runtime=runtime, publisher=publisher,
            source_token=facts().source_token, source_fact_id=lambda pointer: str(uuid5(NAMESPACE_URL, f"fact:{pointer.ticker}:{pointer.boundary_ms}")))
        runtime.last_event_time = at
        await runtime.initialize()
        runtime.order_manager.on_market_snapshot(ExecutionMarketSnapshot("TEST", 9.98, 10., .01, at, "qmd-history"))
        await runtime.risk.prime(broker, ("A",))
        try:
            coordinator = StrategyFortyFiveCoordinator(account_id="A", session_date=day,
                source_token=facts().source_token, port=port)
            state = await coordinator.decide((facts(),), assignment_ids={"TEST": "X"})
            assert len(state.legs) == 15
            assert all(leg.outcome == "submitted" for leg in state.legs)
            assert len(runtime.order_manager._groups) == 15
            orders = await broker.live_orders()
            assert len(orders) == 45
            assert sum(row.side == "BUY" for row in orders) == 15
            assert sum(row.orderType == "STP" for row in orders) == 15
            assert len(portfolio.reservations) == 15
            assert publisher.fenced_sequence == journal.latest_sequence(run_id)
            def bar(boundary):
                from src.backend.backtest_market_data import market_day_boundary
                stamp = market_day_boundary(day, boundary)
                last_us = int(stamp.timestamp() * 1_000_000) - 1
                return dict(ticker="TEST", resolution_ms=100,
                    bucket_index=144_000 + boundary // 100 - 1, event_count=3,
                    last_event_us=last_us, quote_valid=1, quote_timestamp_us=last_us - 10,
                    bid_int=99_800, ask_int=100_000, bid_size=100_000, ask_size=100_000,
                    price_valid=1, extremes_valid=1, close_int=100_000, low_int=99_800,
                    high_int=100_000, execution_volume=100_000.,
                    execution_price_levels=({"price_int": 100_000, "volume": 100_000.},)), stamp
            row, stamp = bar(330_100)
            row.update(ask_size=40, execution_volume=40.,
                execution_price_levels=({"price_int":100_000, "volume":40.},))
            assert await broker.on_liquidity_bar(row, at=stamp)
            await runtime.order_manager.reconcile()
            early = await broker.trades()
            assert sum(trade.size for trade in early) == 10
            first_group = runtime.order_manager._groups[state.legs[0].group_id]
            assert {str(trade.order_id) for trade in early} <= set(first_group.broker_order_roles)
            protections = [order for order in await broker.live_orders()
                if str(order.orderId) in first_group.broker_order_roles
                and first_group.broker_order_roles[str(order.orderId)] != "entry"
                and str(order.order_status.value) in {"Submitted", "PreSubmitted"}]
            assert protections and all(order.totalSize == 10 for order in protections)
            if invalidate == "liquidity":
                from dataclasses import replace
                from types import SimpleNamespace
                trigger = replace(facts().liquidity, decision_ms=360_000, completed_ms=360_000,
                    trades_60s=58, fact_id=str(uuid4()))
                recovery = replace(trigger, decision_ms=390_000,completed_ms=390_000,trades_60s=100)
                port.liquidity_book = SimpleNamespace(fact=lambda ticker, boundary: trigger if boundary == 360_000 else recovery)
                row, stamp = bar(360_000)
                row.update(ask_size=0,execution_volume=0.,execution_price_levels=())
                await broker.on_liquidity_bar(row,at=stamp)
                runtime.last_event_time = stamp
                await port.cancel_liquidity_failure(state=state,fact=trigger,at=stamp)
                await port.complete_liquidity_exits(states={"TEST":state},at=stamp)
                working = [o for o in await broker.live_orders() if o.order_status.value in {"Submitted","PreSubmitted","Inactive"}]
                assert not any(o.side == "BUY" and o.remainingQuantity > 0 for o in working)
                assert len(port._leg_liquidations) == 1
                assert len(runtime.order_manager._groups) == 16
                assert broker.position_quantity("A",123,"TEST") == 10
                # Liquidity can recover without withdrawing the latched sale.
                row, stamp = bar(390_000)
                row.update(execution_volume=0.,execution_price_levels=(),bid_size=0)
                await broker.on_liquidity_bar(row,at=stamp)
                runtime.last_event_time = stamp
                await port.complete_liquidity_exits(states={"TEST":state},at=stamp)
                assert len(runtime.order_manager._groups) == 16
                assert port._liquidity_failures["TEST"] == trigger
                row, stamp = bar(390_100)
                row.update(execution_price_levels=({"price_int":99800,"volume":100000.},))
                await broker.on_liquidity_bar(row,at=stamp)
                await runtime.order_manager.reconcile()
                await port._fence()
                assert broker.position_quantity("A",123,"TEST") == 0
                await port.cancel_session_acquisitions(at=bar(facts().session_end_ms-300_000)[1])
                await port.terminal_receipt()
                return
            if invalidate:
                from types import SimpleNamespace
                at = bar(341_000)[1]
                fact_id = port.source_fact_id(SimpleNamespace(ticker="TEST",boundary_ms=341_000))
                await port.cancel_invalidated_batch(state=state,at=at,
                    feature=dict(boundary_ms=341_000,observed=True,low=9.,fact_id=fact_id))
                working = [o for o in await broker.live_orders()
                    if o.order_status.value in {"Submitted","PreSubmitted","Inactive"}]
                assert not any(o.side == "BUY" and o.remainingQuantity > 0 for o in working)
                assert broker.position_quantity("A",123,"TEST") == 10
                active_stops = [o for o in working if o.orderType == "STP" and o.order_status.value != "Inactive"]
                assert sum(o.remainingQuantity for o in active_stops) == 10
                return
            row, stamp = bar(341_000)
            row.update(ask_size=0,execution_volume=0.,execution_price_levels=())
            await broker.on_liquidity_bar(row,at=stamp)
            runtime.last_event_time = stamp
            partial_facts = await port.completed_leg_facts(state=state,
                feature=dict(boundary_ms=341_000,observed=True,high=10.,ten_second_mean_movement=.01),
                bid=9.98,quote_valid=True,quote_age_us=0)
            await coordinator.manage("TEST",partial_facts)
            stops = [o for o in await broker.live_orders()
                if str(o.orderId) in first_group.broker_order_roles and o.orderType == "STP"
                and o.order_status.value in {"Submitted","PreSubmitted","Inactive"}]
            assert len(stops) == 2 and all(o.auxPrice >= 9.9 for o in stops)
            row, stamp = bar(341_100)
            assert await broker.on_liquidity_bar(row, at=stamp)
            await runtime.order_manager.reconcile()
            await port._fence()
            assert broker.position_quantity("A", 123, "TEST") == sum(leg.quantity for leg in state.batch.legs)
            await runtime._canonical_session.reconcile()
            from src.backend.canonical_trading_service import trading_state_payload
            presentation = trading_state_payload(runtime.projected_snapshot(),include_strategy_activity=False)
            assert len(presentation["position_lifecycles"]) == 15
            assert len({p["lifecycle_id"] for p in presentation["position_lifecycles"]}) == 15
            assert presentation["portfolio"]["position_count"] == 15
            feature = dict(boundary_ms=342_000, observed=True, high=10., ten_second_mean_movement=.01)
            native_facts = await port.completed_leg_facts(state=state, feature=feature,
                bid=9.98, quote_valid=True, quote_age_us=10)
            assert len(native_facts) == 15
            assert all(row.first_fill_ms in (330_100, 341_100) and row.average_entry == 10. for row in native_facts)
            assert [row.held_quantity for row in native_facts] == [leg.quantity for leg in state.batch.legs]
            row, cutoff = bar(facts().session_end_ms - 300_000)
            await broker.on_liquidity_bar(row, at=cutoff)
            runtime.last_event_time = cutoff
            runtime.order_manager.on_market_snapshot(ExecutionMarketSnapshot("TEST", 9.98, 10., .01, cutoff, "qmd-history"))
            await port.cancel_session_acquisitions(at=cutoff)
            runtime.last_event_time = bar(facts().session_end_ms - 60_000)[1]
            original = {key: group for key, group in runtime.order_manager._groups.items()}
            exit_group = await port.liquidate_leg(state=state, ordinal=1, bid=9.98, source_fact_id=str(uuid4()))
            assert exit_group.action == "exit"
            assert len(runtime.order_manager._groups) == 16
            assert original[state.legs[0].group_id].protection_delegated
            assert all(not group.protection_delegated for key, group in original.items() if key != state.legs[0].group_id)
            from src.trading_runtime.ibkr_schema import OPEN_ORDER_STATUSES
            live = [order for order in await broker.live_orders() if order.order_status in OPEN_ORDER_STATUSES]
            assert len(live) == 29  # 14 sibling pairs plus this leg's dedicated exit.
            row, stamp = bar(facts().session_end_ms - 59_900)
            assert await broker.on_liquidity_bar(row, at=stamp)
            await runtime.order_manager.reconcile()
            await port._fence()
            assert broker.position_quantity("A", 123, "TEST") == sum(leg.quantity for leg in state.batch.legs[1:])
            native_facts = await port.completed_leg_facts(state=state,
                feature={**feature, "boundary_ms": facts().session_end_ms - 9000},
                bid=9.98, quote_valid=True, quote_age_us=10)
            assert native_facts[0].held_quantity == 0
            assert native_facts[0].latest_fill_ms == facts().session_end_ms - 59_900
            assert [row.held_quantity for row in native_facts[1:]] == [leg.quantity for leg in state.batch.legs[1:]]
            with pytest.raises(RuntimeError, match="residual native exposure"):
                await port.terminal_receipt()
            for ordinal in range(2, 16):
                await port.liquidate_leg(state=state, ordinal=ordinal, bid=9.98, source_fact_id=str(uuid4()))
            row, stamp = bar(facts().session_end_ms - 59_800)
            assert await broker.on_liquidity_bar(row, at=stamp)
            await runtime.order_manager.reconcile()
            receipt = await port.terminal_receipt()
            assert receipt["remaining_positions"] == receipt["working_orders"] == 0
            assert receipt["liquidation_orders"] == 15
            assert receipt["last_sequence"] == journal.latest_sequence(run_id)
        finally:
            await runtime.order_manager.close()
            journal.close()
    asyncio.run(run())
