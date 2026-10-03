import asyncio
import pytest
from datetime import date
from uuid import uuid4

from src.backend.backtest_strategy_forty_three_journal import StrategyFortyThreeJournal
from src.backend.backtest_strategy_forty_three_native import StrategyFortyThreeNativePort
from src.backend.backtest_strategy_forty_three_publisher import StrategyFortyThreePublisher
from src.trading_runtime.domain import InstrumentContract, TradingMode
from src.trading_runtime.execution_policies import ExecutionMarketSnapshot
from src.trading_runtime.portfolio import PortfolioAccountProfile, PortfolioManagementEngine, PortfolioPolicy
from src.trading_runtime.runtime import TradingRuntime, RunConfig, RunMode
from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter, SimulationConfig
from src.trading_runtime.strategy_engine import StrategyAssignment, StrategyPermissions, AssignmentStatus
from src.trading_runtime.strategy_forty_three_coordinator import StrategyFortyThreeCoordinator
from src.trading_runtime.strategy_forty_three_rules import entry_intents, propose_batch
from src.trading_runtime.strategy_forty_three_runtime import AssignedStrategyFortyThree
from src.trading_runtime.strategy_forty_three_orders import StrategyFortyThreeOrderPlanner
from tests.test_backtest_typed_publisher import FakeWriter
from tests.test_strategy_forty_three_rules import facts


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
                await StrategyFortyThreeNativePort._fence(port)
            assert publisher.calls == 32
        else:
            receipt = await StrategyFortyThreeNativePort._fence(port)
            assert receipt.last_sequence == journal.sequence
            assert publisher.calls == changes + 1
    asyncio.run(run(1))
    asyncio.run(run(32))


def test_fenced_batch_uses_actual_native_portfolio_and_oms_for_fifteen_parents():
    async def run():
        day = date(2026, 9, 3)
        batch = propose_batch(facts(), account_id="A", assignment_id="X",
            free_cash_after_reservations=10_000., already_submitted=False)
        at = entry_intents(batch, session_date=day)[0].event_time
        run_id = str(uuid4())
        journal = StrategyFortyThreeJournal(run_id=run_id)
        assignment = StrategyAssignment("X", "squeeze-grid-strategy", 43, "A", "TEST", 123,
            AssignmentStatus.WATCHING, StrategyPermissions(enter=True), {})
        broker = SimulatedBrokerAdapter(["A"], SimulationConfig(initial_cash=10_000.),
            mode=TradingMode.BACKTEST, initial_time=at, fixed_bar_mode=True)
        await broker.initialize()
        policy = PortfolioPolicy(maximum_position_fraction=1., maximum_ticker_fraction=1.,
            maximum_planned_risk_fraction=.5, maximum_open_risk_fraction=.5,
            allow_outside_rth=True, entry_fee_buffer_bps=0.)
        portfolio = PortfolioManagementEngine([PortfolioAccountProfile("cash", "A", "backtest",
            "simulated", policy)], journal=journal, run_id=run_id,
            strategy_id="squeeze-grid-strategy", strategy_revision=43, event_clock=lambda: at)
        planner = StrategyFortyThreeOrderPlanner({"TEST": InstrumentContract(
            "conid:123", 123, "TEST", "STK", "SMART", "USD")}, run_id=run_id)
        runtime = TradingRuntime(config=RunConfig(RunMode.BACKTEST, "squeeze-grid-strategy", 43,
            ("A",), day, run_id=run_id, safety_supervisor_enabled=False,
            write_progress_checkpoints=False), broker=broker,
            strategy=AssignedStrategyFortyThree([assignment]), journal=journal,
            portfolio=portfolio, intent_planner=planner)
        writer = FakeWriter()
        writer.run_id, writer.journal_profile = run_id, "backtest_v4"
        writer.submit_base_v4 = writer.submit
        writer.submit_compound_v4 = lambda unit, **_: writer.submit(unit.base)
        publisher = StrategyFortyThreePublisher(journal, writer, attempt_id=str(uuid4()),
            run_month=day.replace(day=1), expected_config={"mode": "backtest",
                "strategy_id": "squeeze-grid-strategy", "strategy_revision": 43})
        port = StrategyFortyThreeNativePort(runtime=runtime, publisher=publisher,
            source_token=facts().source_token, source_fact_id=lambda _: str(uuid4()))
        runtime.last_event_time = at
        runtime.order_manager.on_market_snapshot(ExecutionMarketSnapshot("TEST", 9.98, 10., .01, at, "qmd-history"))
        await runtime.risk.prime(broker, ("A",))
        try:
            coordinator = StrategyFortyThreeCoordinator(account_id="A", session_date=day,
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
            row, stamp = bar(20_100)
            assert await broker.on_liquidity_bar(row, at=stamp)
            await runtime.order_manager.reconcile()
            await port._fence()
            assert broker.position_quantity("A", 123, "TEST") == sum(leg.quantity for leg in state.batch.legs)
            feature = dict(boundary_ms=21_000, observed=True, high=10., ten_second_mean_movement=.01)
            native_facts = await port.completed_leg_facts(state=state, feature=feature,
                bid=9.98, quote_valid=True, quote_age_us=10)
            assert len(native_facts) == 15
            assert all(row.first_fill_ms == 20_100 and row.average_entry == 10. for row in native_facts)
            assert [row.held_quantity for row in native_facts] == [leg.quantity for leg in state.batch.legs]
            row, cutoff = bar(facts().session_end_ms - 10_000)
            await broker.on_liquidity_bar(row, at=cutoff)
            runtime.last_event_time = cutoff
            runtime.order_manager.on_market_snapshot(ExecutionMarketSnapshot("TEST", 9.98, 10., .01, cutoff, "qmd-history"))
            await port.cancel_session_acquisitions(at=cutoff)
            original = {key: group for key, group in runtime.order_manager._groups.items()}
            exit_group = await port.liquidate_leg(state=state, ordinal=1, bid=9.98, source_fact_id=str(uuid4()))
            assert exit_group.action == "exit"
            assert len(runtime.order_manager._groups) == 16
            assert original[state.legs[0].group_id].protection_delegated
            assert all(not group.protection_delegated for key, group in original.items() if key != state.legs[0].group_id)
            from src.trading_runtime.ibkr_schema import OPEN_ORDER_STATUSES
            live = [order for order in await broker.live_orders() if order.order_status in OPEN_ORDER_STATUSES]
            assert len(live) == 29  # 14 sibling pairs plus this leg's dedicated exit.
            row, stamp = bar(facts().session_end_ms - 9900)
            assert await broker.on_liquidity_bar(row, at=stamp)
            await runtime.order_manager.reconcile()
            await port._fence()
            assert broker.position_quantity("A", 123, "TEST") == sum(leg.quantity for leg in state.batch.legs[1:])
            native_facts = await port.completed_leg_facts(state=state,
                feature={**feature, "boundary_ms": facts().session_end_ms - 9000},
                bid=9.98, quote_valid=True, quote_age_us=10)
            assert native_facts[0].held_quantity == 0
            assert native_facts[0].latest_fill_ms == facts().session_end_ms - 9900
            assert [row.held_quantity for row in native_facts[1:]] == [leg.quantity for leg in state.batch.legs[1:]]
            with pytest.raises(RuntimeError, match="residual native exposure"):
                await port.terminal_receipt()
            for ordinal in range(2, 16):
                await port.liquidate_leg(state=state, ordinal=ordinal, bid=9.98, source_fact_id=str(uuid4()))
            row, stamp = bar(facts().session_end_ms - 9800)
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
