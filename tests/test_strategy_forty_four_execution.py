import asyncio
from datetime import date
from dataclasses import replace
from types import SimpleNamespace, MappingProxyType
from uuid import uuid4

from src.backend.backtest_strategy_forty_four_journal import StrategyFortyFourJournal
from src.backend.backtest_strategy_forty_four_native import StrategyFortyFourNativePort
from src.backend.backtest_strategy_forty_four_publisher import StrategyFortyFourPublisher
from src.backend.backtest_strategy_forty_four_plan import FortyFourSourcePlan
from src.backend.backtest_strategy_forty_four_source import certify_history
import src.backend.backtest_strategy_forty_four_execution as execution
from src.backend.backtest_market_data import market_day_boundary
from src.trading_runtime.domain import InstrumentContract, TradingMode
from src.trading_runtime.portfolio import PortfolioAccountProfile, PortfolioManagementEngine, PortfolioPolicy
from src.trading_runtime.runtime import RunConfig, RunMode, typed_run_config_payload
from src.trading_runtime.strategy_forty_four_runtime import StrategyFortyFourRuntime as TradingRuntime
from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter, SimulationConfig
from src.trading_runtime.strategy_engine import StrategyAssignment, StrategyPermissions, AssignmentStatus
from src.trading_runtime.strategy_forty_four_runtime import AssignedStrategyFortyFour
from src.trading_runtime.strategy_forty_four_orders import StrategyFortyFourOrderPlanner
from tests.test_backtest_typed_publisher import FakeWriter
from tests.test_strategy_forty_four_rules import facts
from tests.test_strategy_forty_four_source import fixture


def test_session_runner_uses_actual_native_orders_later_fills_and_fenced_liquidation(monkeypatch):
    state, arguments = fixture(monkeypatch)
    history = certify_history(**arguments)
    plan = FortyFourSourcePlan(arguments["market"], arguments["identity"], None, arguments["structure"],
        arguments["snapshot_hash"], arguments["signal_query_hash"], MappingProxyType({"TEST": 6000}),
        MappingProxyType({"TEST": state["population"]}), 330_000)
    def bar(boundary):
        stamp = market_day_boundary("2026-09-03", boundary)
        last_us = int(stamp.timestamp() * 1_000_000) - 1
        return dict(ticker="TEST", resolution_ms=100, boundary_ms=boundary,
            bucket_index=144_000 + boundary // 100 - 1, event_count=3,
            last_event_us=last_us, quote_valid=1, quote_timestamp_us=last_us,
            bid_int=99_800, ask_int=100_000, bid_size=100_000, ask_size=100_000,
            price_valid=1, extremes_valid=1, close_int=100_000, low_int=99_800,
            high_int=100_000, execution_volume=100_000.,
            execution_price_levels=({"price_int": 100_000, "volume": 100_000.},))
    def read_admissions(_plan, _history, _reader, **kwargs):
        kwargs["completed_market_rows"].append(bar(6000))
        return (facts(boundary_ms=6000, admission_ms=6000, session_end_ms=330_000,
            swing_available_ms=5000, structural_boundary_ms=6000, source_token=history.token),)
    monkeypatch.setattr(execution, "load_admission_facts", read_admissions)
    monkeypatch.setattr(execution, "project_market_day_plan", lambda market, _: market)
    monkeypatch.setattr(execution, "iter_market_day_rows", lambda *_a, **kwargs:
        iter(bar(ms) for ms in range(kwargs["after_boundary_ms"]+100, kwargs["through_boundary_ms"]+1, 100)))
    class Reader:
        def close(self):
            pass
    async def run():
        day, run_id = date(2026, 9, 3), str(uuid4())
        start = market_day_boundary(day, 0)
        journal = StrategyFortyFourJournal(run_id=run_id)
        assignment = StrategyAssignment("X", "squeeze-grid-strategy", 44, "A", "TEST", 123,
            AssignmentStatus.WATCHING, StrategyPermissions(enter=True), {})
        broker = SimulatedBrokerAdapter(["A"], SimulationConfig(initial_cash=10_000.),
            mode=TradingMode.BACKTEST, initial_time=start, fixed_bar_mode=True)
        policy = PortfolioPolicy(maximum_position_fraction=1., maximum_ticker_fraction=1.,
            maximum_planned_risk_fraction=1., maximum_open_risk_fraction=1.,
            allow_outside_rth=True, entry_fee_buffer_bps=0.)
        portfolio = PortfolioManagementEngine([PortfolioAccountProfile("cash", "A", "backtest",
            "simulated", policy)], journal=journal, run_id=run_id,
            strategy_id="squeeze-grid-strategy", strategy_revision=44, event_clock=lambda: runtime.last_event_time)
        planner = StrategyFortyFourOrderPlanner({"TEST": InstrumentContract(
            "conid:123", 123, "TEST", "STK", "SMART", "USD")}, run_id=run_id)
        runtime = TradingRuntime(config=RunConfig(RunMode.BACKTEST, "squeeze-grid-strategy", 44,
            ("A",), day, run_id=run_id, safety_supervisor_enabled=False,
            write_progress_checkpoints=False), broker=broker, strategy=AssignedStrategyFortyFour([assignment]),
            journal=journal, portfolio=portfolio, intent_planner=planner)
        writer = FakeWriter()
        writer.run_id, writer.journal_profile = run_id, "backtest_v4"
        writer.submit_base_v4 = writer.submit
        writer.submit_compound_v4 = lambda unit, **_: writer.submit(unit.base)
        publisher = StrategyFortyFourPublisher(journal, writer, attempt_id=str(uuid4()),
            run_month=day.replace(day=1), expected_config=typed_run_config_payload(runtime.config))
        port = StrategyFortyFourNativePort(runtime=runtime, publisher=publisher,
            source_token=history.token, source_fact_id=history.fact_id, session_end_ms=330_000)
        runtime.last_event_time = start
        await runtime.initialize()
        # Fixture transport only; all financial actors and journal projectors are real.
        async def before(*_):
            pass
        async def finish(*_):
            await port._fence()
        monkeypatch.setattr(type(history), "load_active_history", lambda *_: tuple(state["facts"]))
        try:
            receipt = await execution.run_session(plan=plan, history=history,
                prices=SimpleNamespace(projected=lambda _: None), runtime=runtime, port=port,
                client_factory=Reader, before_boundary=before, finish_boundary=finish)
            assert receipt["ticker_batches"] == 1 and receipt["liquidation_orders"] == 15
            assert receipt["remaining_positions"] == receipt["working_orders"] == 0
            trades = await broker.trades()
            assert len(trades) == 30
            assert all(row.trade_time > market_day_boundary(day, 6000) for row in trades)
            assert publisher.fenced_sequence == journal.latest_sequence(run_id)
        finally:
            pending = getattr(publisher, "_task", None)
            if pending is not None and not pending.done():
                await pending
            await runtime.order_manager.close()
            journal.close()
    asyncio.run(run())
