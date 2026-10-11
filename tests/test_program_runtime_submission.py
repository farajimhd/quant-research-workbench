"""Synthetic qualification of the shared runtime, Portfolio, OMS and broker."""
import asyncio
from dataclasses import replace
from datetime import timedelta, datetime, timezone

import pytest

from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.trading_runtime.columnar_program_coordinator import select_program_proposal
from src.trading_runtime.columnar_program_actor import AssignedProgramStrategy
from src.trading_runtime.execution_policies import ExecutionPolicy, ExecutionEnvelope, ProtectionProfile, ProtectionSlice, StopRule
from src.trading_runtime.runtime import TradingRuntime, RunConfig, RunMode
from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter, SimulationConfig
from src.trading_runtime.strategy_engine import StrategyAssignment, StrategyPermissions, AssignmentStatus
from src.trading_runtime.strategy_orders import RuntimeIbkrStrategyOrderPlanner, InstrumentContract
from tests.test_columnar_program_coordinator import inputs
from tests.test_broker_liquidity_resolution import second_bar


async def scenario(resolution, invalid=None, initial_cash=100000., stop_exit=False):
    release, boundary, position, management = inputs(entry=True)
    at = datetime(2026, 10, 12, 14, tzinfo=timezone.utc)
    boundary = replace(boundary, boundary_at=at, available_at=at)
    management = replace(management, observed_at=at)
    assignment = StrategyAssignment("assignment", "shared-program", 62, "account", "ABC", 1,
        AssignmentStatus.WATCHING, StrategyPermissions(enter=True, add=True), {})
    strategy = AssignedProgramStrategy(release, (assignment,), {"assignment": "listing"})
    broker = SimulatedBrokerAdapter(["account"],
        SimulationConfig(liquidity_resolution_ms=resolution, initial_cash=initial_cash), mode=RunMode.BACKTEST,
        initial_time=boundary.boundary_at - timedelta(seconds=1), fixed_bar_mode=True)
    journal = BacktestMemoryJournal(run_id=release.header["release_id"])
    runtime = TradingRuntime(RunConfig(RunMode.BACKTEST, "shared-program", 62, ("account",),
        boundary.boundary_at.date(), run_id=journal.run_id, safety_supervisor_enabled=False,
        write_progress_checkpoints=False), broker, strategy, journal,
        intent_planner=RuntimeIbkrStrategyOrderPlanner(
            {"ABC": InstrumentContract("ibkr:1", 1, "ABC", "STK", "USD")},
            strategy_id="shared-program", strategy_revision=62, run_id=journal.run_id))
    await runtime.initialize()
    try:
        row = second_bar(boundary.boundary_at)
        row.update(ticker="ABC", ask_int=105000, bid_int=104900, close_int=105000,
                   high_int=105100, low_int=104800)
        if resolution == 100:
            row.update(resolution_ms=100, bucket_index=(row["bucket_index"] + 1) * 10 - 1)
        await runtime.process_liquidity_boundary((row,), at=boundary.boundary_at)
        assert runtime.execution_market_data.snapshot("ABC").source == broker.config.liquidity_source
        proposal = select_program_proposal(release, boundary, position=position, management=management)[0]
        kwargs = dict(account_id="account", assignment_id="assignment",
            execution_policy=ExecutionPolicy(envelope=ExecutionEnvelope(deadline_ms=2000)),
            protection_profile=ProtectionProfile("test", 1,
                (ProtectionSlice("one", 1., StopRule(price=9.)),)), outside_rth=False)
        if invalid == "cash":
            proposal = replace(proposal, requested_cash=2000.)
        elif invalid == "assignment":
            kwargs["assignment_id"] = "other"
        elif invalid == "future":
            proposal = replace(proposal, boundary_at=proposal.boundary_at + timedelta(seconds=1))
        elif invalid == "deadline":
            kwargs["execution_policy"] = ExecutionPolicy()
        before = journal.latest_sequence(journal.run_id)
        if invalid:
            with pytest.raises(ValueError):
                await runtime.submit_program_proposal(proposal, **kwargs)
            assert journal.latest_sequence(journal.run_id) == before
            assert not broker._orders
            return
        result = await runtime.submit_program_proposal(proposal, **kwargs)
        if initial_cash < 1000:
            assert result[0]["decision"]["status"] == "rejected"
            assert result[0]["decision"]["reasons"]
            assert not broker._orders
            assert broker.position_quantity("account", 1, "ABC") == 0
            return
        assert result[0]["decision"]["status"] in {"approved", "resized"}, result
        assert broker.position_quantity("account", 1, "ABC") == 0
        assert not strategy.position_for("assignment").position_open
        intents = [r for r in journal.records(journal.run_id) if r.entity_type == "strategy_intent"]
        assert len(intents) == 1 and intents[0].payload["metadata"] == {}
        at = boundary.boundary_at + timedelta(seconds=1)
        row = {**row, "last_event_us": int(at.timestamp() * 1_000_000) - 1,
               "quote_timestamp_us": int(at.timestamp() * 1_000_000) - 10001,
               "bucket_index": row["bucket_index"] + 1000 // resolution}
        await runtime.process_liquidity_boundary((row,), at=at)
        assert broker.position_quantity("account", 1, "ABC") > 0, repr(broker._orders)
        actual = strategy.position_for("assignment")
        assert actual.position_open and actual.opened_at <= at
        assert float(actual.quantity) == broker.position_quantity("account", 1, "ABC")
        assert actual.fees > 0 and strategy.execution_state.pending_fee_count == 0
        # The callback receives the broker adapter's typed execution objects;
        # canonical_executions is a different domain projection.
        executions = tuple(broker._executions)
        strategy.on_broker_executions(executions, runtime.order_manager)
        assert strategy.position_for("assignment") == actual
        before = strategy.position_for("assignment")
        with pytest.raises(ValueError, match="identity conflicts"):
            strategy.on_broker_executions((replace(executions[0], order_ref="other-order"),), runtime.order_manager)
        assert strategy.position_for("assignment") == before
        assert any(r.entity_type == "fill" for r in journal.records(journal.run_id))
        assert any(r.entity_type == "commission" for r in journal.records(journal.run_id))
        assert result[0]["decision"]["approved_notional"] <= 1000
        assert result[0]["decision"]["approved_notional"] <= initial_cash
        if stop_exit:
            at += timedelta(seconds=1)
            row.update(last_event_us=int(at.timestamp() * 1_000_000) - 1,
                quote_timestamp_us=int(at.timestamp() * 1_000_000) - 10001,
                bucket_index=row["bucket_index"] + 1000 // resolution,
                bid_int=84900, ask_int=85000, close_int=85000, low_int=84000, high_int=86000)
            await runtime.process_liquidity_boundary((row,), at=at)
            # Entry deadline expires its partial remainder and OMS repairs the
            # actual held quantity. That repair first becomes eligible in the
            # following complete liquidity bucket.
            at += timedelta(seconds=1)
            row.update(last_event_us=int(at.timestamp() * 1_000_000) - 1,
                quote_timestamp_us=int(at.timestamp() * 1_000_000) - 10001,
                bucket_index=row["bucket_index"] + 1000 // resolution)
            await runtime.process_liquidity_boundary((row,), at=at)
            closed = strategy.position_for("assignment")
            assert not closed.position_open and closed.closed_positions == 1, repr(broker._orders)
            assert closed.fees > actual.fees and closed.net_realized_pnl < 0
            assert broker.position_quantity("account", 1, "ABC") == 0
            rebuilt = AssignedProgramStrategy(release, (assignment,), {"assignment": "listing"})
            rebuilt.on_broker_executions(tuple(broker._executions), runtime.order_manager)
            assert rebuilt.execution_state.finalized_positions() == strategy.execution_state.finalized_positions()
    finally:
        await runtime.finish()
        journal.close()


@pytest.mark.parametrize("resolution", [100, 1000])
def test_program_uses_actual_cash_oms_and_later_broker_fills(resolution):
    asyncio.run(scenario(resolution))


@pytest.mark.parametrize("invalid", ["cash", "assignment", "future", "deadline"])
def test_invalid_proposals_do_not_mutate_journal_or_broker(invalid):
    asyncio.run(scenario(1000, invalid))


def test_portfolio_rejects_request_above_actual_cash_under_its_policy():
    asyncio.run(scenario(1000, initial_cash=100.))


@pytest.mark.parametrize("resolution", [100, 1000])
def test_actual_protective_fill_closes_actor_position_with_fees(resolution):
    asyncio.run(scenario(resolution, stop_exit=True))
