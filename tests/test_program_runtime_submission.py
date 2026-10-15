"""Synthetic qualification of the shared runtime, Portfolio, OMS and broker."""
import asyncio
from dataclasses import replace
from datetime import timedelta, datetime, timezone

import pytest

from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.trading_runtime.columnar_program_coordinator import select_program_proposal
from src.trading_runtime.columnar_program_actor import AssignedProgramStrategy
from src.trading_runtime.oms_assignment_observation import observe_assignment_orders
from src.trading_runtime.execution_policies import ExecutionPolicy, ExecutionEnvelope, ProtectionProfile, ProtectionSlice, StopRule
from src.trading_runtime.runtime import TradingRuntime, RunConfig, RunMode
from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter, SimulationConfig
from src.trading_runtime.strategy_engine import StrategyAssignment, StrategyPermissions, AssignmentStatus
from src.trading_runtime.strategy_orders import RuntimeIbkrStrategyOrderPlanner, InstrumentContract
from tests.test_columnar_program_coordinator import inputs
from tests.test_broker_liquidity_resolution import second_bar


async def scenario(resolution, invalid=None, initial_cash=100000., stop_exit=False, target_reduce=False, cancel_reduce=False, partial_cancel=False, allocation_failure=None, add_before_reduce=False,
                   risk_limit=None, unsupported_mode=None):
    release, boundary, position, management = inputs(entry=True)
    at = datetime(2026, 10, 12, 14, tzinfo=timezone.utc)
    boundary = replace(boundary, boundary_at=at, available_at=at)
    management = replace(management, observed_at=at)
    assignment = StrategyAssignment("assignment", "shared-program", 62, "account", "ABC", 1,
        AssignmentStatus.WATCHING, StrategyPermissions(enter=True, add=True), {})
    risk = None
    if risk_limit is not None:
        from tests.test_configuration_execution_risk import contract
        risk = contract(maximum_position_risk=risk_limit)
    strategy = AssignedProgramStrategy(release, (assignment,), {"assignment": "listing"}, execution_risk=risk)
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
        if unsupported_mode is not None:
            prior = journal.latest_sequence(journal.run_id)
            runtime.config = replace(runtime.config, mode=unsupported_mode, safety_supervisor_enabled=True)
            with pytest.raises(RuntimeError, match='normalized decision receipts'):
                runtime.require_program_dispatch_mode()
            with pytest.raises(RuntimeError, match='normalized decision receipts'):
                runtime._record_executions(())
            assert journal.latest_sequence(journal.run_id) == prior
            assert not broker._orders
            return
        assert runtime.require_program_dispatch_mode() == 'backtest'
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
        elif invalid == "profile":
            kwargs["protection_profile"] = ProtectionProfile("wrong-stop", 1,
                (ProtectionSlice("one", 1., StopRule(price=8.)),))
        before = journal.latest_sequence(journal.run_id)
        if invalid:
            with pytest.raises(ValueError):
                await runtime.submit_program_proposal(proposal, **kwargs)
            assert journal.latest_sequence(journal.run_id) == before
            assert not broker._orders
            return
        result = await runtime.submit_program_proposal(proposal, **kwargs)
        if risk_limit is not None and risk_limit < 150:
            assert result[0]['decision']['status'] == 'rejected'
            assert 'maximum_position_risk_exceeded' in result[0]['decision']['reasons']
            assert not broker._orders
            assert not any(r.status == 'reserved' for r in runtime.portfolio.reservations.values())
            assert journal.latest_sequence(journal.run_id) > before
            return
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
        observation = observe_assignment_orders(runtime.order_manager, account_id="account",
            assignment_id="assignment", ticker="ABC", at=at)
        assert observation.pending_acquisition and not observation.pending_reduction
        assert any(order.role == "entry" and order.remaining_quantity > 0
            for order in observation.working_orders)
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
        if target_reduce:
            phase_price = 110000 if add_before_reduce else 200000
            for jump in (200, 1):
                at += timedelta(seconds=jump)
                row.update(last_event_us=int(at.timestamp() * 1_000_000) - 1,
                    quote_timestamp_us=int(at.timestamp() * 1_000_000) - 10001,
                    bucket_index=row["bucket_index"] + jump * 1000 // resolution,
                    bid_int=phase_price - 100, ask_int=phase_price, close_int=phase_price,
                    low_int=phase_price - 200, high_int=phase_price + 100,
                    execution_price_levels=({"price_int": phase_price, "volume": float(row["execution_volume"])},))
                await runtime.process_liquidity_boundary((row,), at=at)
            # Consume repair-order ACKs through the existing OMS reconciliation;
            # a submit response cannot be substituted for processed protection.
            await runtime.order_manager.reconcile()
            observation = observe_assignment_orders(runtime.order_manager, account_id="account",
                assignment_id="assignment", ticker="ABC", at=at)
            assert not observation.pending_acquisition
            stops = [order.stop_price for order in observation.working_orders if order.role == "protective_stop"]
            assert stops, (observation, strategy.position_for("assignment"),
                [(group.state, group.broker_order_roles, group.filled_by_broker_order,
                    group.terminal_broker_order_ids) for group in runtime.order_manager._groups.values()])
            stop = stops[0]
            target_before = strategy.target_state.target_for(strategy.position_for("assignment"))
            if add_before_reduce:
                signals = dict(boundary.rules, add=True)
                add_proposal, = strategy.proposals(replace(boundary, boundary_at=at, available_at=at,
                    close=11., rules=signals), assignment_id="assignment", management=replace(management,
                    observed_at=at, stop_price=stop, target_price=float(target_before),
                    high_water_price=observation.high_water_price))
                assert add_proposal.requested_cash == 200.
                await runtime.submit_program_proposal(add_proposal, **kwargs)
                at += timedelta(seconds=1)
                row.update(last_event_us=int(at.timestamp() * 1_000_000) - 1,
                    quote_timestamp_us=int(at.timestamp() * 1_000_000) - 10001,
                    bucket_index=row["bucket_index"] + 1000 // resolution)
                await runtime.process_liquidity_boundary((row,), at=at)
                assert strategy.position_for("assignment").adds == 1
                assert strategy.target_state.target_for(strategy.position_for("assignment")) == target_before
                at += timedelta(seconds=200)
                row.update(last_event_us=int(at.timestamp() * 1_000_000) - 1,
                    quote_timestamp_us=int(at.timestamp() * 1_000_000) - 10001,
                    bucket_index=row["bucket_index"] + 200 * 1000 // resolution,
                    bid_int=199900, ask_int=200000, close_int=200000, low_int=199800, high_int=200100,
                    execution_price_levels=({"price_int": 200000, "volume": float(row["execution_volume"])},))
                await runtime.process_liquidity_boundary((row,), at=at)
            selected = strategy.proposals(replace(boundary, boundary_at=at, available_at=at, close=20.),
                assignment_id="assignment", management=replace(management, observed_at=at,
                    stop_price=stop, target_price=float(target_before),
                    high_water_price=observation.high_water_price))
            assert len(selected) == 1 and selected[0].reason == "program:target"
            if allocation_failure:
                method = "modify_order" if allocation_failure in {"modify", "cancel"} else "place_orders"
                original = getattr(broker, method)
                failed = False
                async def fail_once(*args, **kw):
                    nonlocal failed
                    if not failed:
                        failed = True
                        if allocation_failure == "cancel":
                            await original(*args, **kw)
                            raise asyncio.CancelledError()
                        if allocation_failure == "lost_response":
                            await original(*args, **kw)
                            raise TimeoutError("synthetic lost broker response")
                        raise ValueError("synthetic allocation rejection")
                    return await original(*args, **kw)
                setattr(broker, method, fail_once)
                try:
                    expected = (asyncio.CancelledError if allocation_failure == "cancel" else
                        TimeoutError if allocation_failure == "lost_response" else ValueError)
                    with pytest.raises(expected):
                        await runtime.submit_program_proposal(selected[0], account_id="account", assignment_id="assignment",
                            execution_policy=kwargs["execution_policy"], protection_profile=None, outside_rth=False)
                    if allocation_failure == "lost_response":
                        snapshot = runtime.order_manager.snapshot_for_intent(selected[0].intent_id)
                        assert snapshot.state.value == "outcome_unknown"
                        open_orders = [order for order in await broker.live_orders()
                            if str(order.order_status) in {"Submitted", "PreSubmitted"}]
                        assert sum(float(order.remainingQuantity) for order in open_orders
                            if order.side == "SELL") == float(strategy.position_for("assignment").quantity)
                        assert strategy.target_state.target_for(strategy.position_for("assignment")) == target_before
                        return
                    await runtime.order_manager.reconcile()
                    observation = observe_assignment_orders(runtime.order_manager, account_id="account",
                        assignment_id="assignment", ticker="ABC", at=at)
                    assert not observation.pending_reduction
                    assert sum(order.remaining_quantity for order in observation.working_orders
                        if order.role == "protective_stop") == float(strategy.position_for("assignment").quantity)
                    assert strategy.target_state.target_for(strategy.position_for("assignment")) == target_before
                finally:
                    setattr(broker, method, original)
                return
            await runtime.submit_program_proposal(selected[0], account_id="account", assignment_id="assignment",
                execution_policy=kwargs["execution_policy"], protection_profile=None, outside_rth=False)
            assert strategy.target_state.target_for(strategy.position_for("assignment")) == target_before
            if cancel_reduce:
                reduction = next(group for group in runtime.order_manager._groups.values()
                    if group.intent.intent_id == selected[0].intent_id)
                if partial_cancel:
                    at += timedelta(seconds=1)
                    row.update(last_event_us=int(at.timestamp() * 1_000_000) - 1,
                        quote_timestamp_us=int(at.timestamp() * 1_000_000) - 10001,
                        bucket_index=row["bucket_index"] + 1000 // resolution, bid_size=20.)
                    await runtime.process_liquidity_boundary((row,), at=at)
                    assert strategy.position_for("assignment").quantity == 20
                    assert strategy.target_state.target_for(strategy.position_for("assignment")) > target_before
                retained_target = strategy.target_state.target_for(strategy.position_for("assignment"))
                for order_id in reduction.broker_order_ids:
                    await broker.cancel_order("account", order_id)
                await runtime.order_manager.reconcile()
                observation = observe_assignment_orders(runtime.order_manager, account_id="account",
                    assignment_id="assignment", ticker="ABC", at=at)
                assert not observation.pending_reduction
                stops = [order for order in observation.working_orders if order.role == "protective_stop"]
                assert sum(order.remaining_quantity for order in stops) == float(strategy.position_for("assignment").quantity)
                assert strategy.target_state.target_for(strategy.position_for("assignment")) == retained_target
                return
            at += timedelta(seconds=1)
            row.update(last_event_us=int(at.timestamp() * 1_000_000) - 1,
                quote_timestamp_us=int(at.timestamp() * 1_000_000) - 10001,
                bucket_index=row["bucket_index"] + 1000 // resolution)
            await runtime.process_liquidity_boundary((row,), at=at)
            assert strategy.target_state.target_for(strategy.position_for("assignment")) > target_before
            if add_before_reduce:
                await runtime.order_manager.reconcile()
                protected = observe_assignment_orders(runtime.order_manager, account_id="account",
                    assignment_id="assignment", ticker="ABC", at=at)
                assert sum(order.remaining_quantity for order in protected.working_orders
                    if order.role == "protective_stop") == float(strategy.position_for("assignment").quantity)
            rebuilt = AssignedProgramStrategy(release, (assignment,), {"assignment": "listing"})
            rebuilt.on_broker_executions(tuple(broker._executions), runtime.order_manager)
            assert rebuilt.target_state.target_for(rebuilt.position_for("assignment")) == strategy.target_state.target_for(strategy.position_for("assignment"))
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


@pytest.mark.parametrize("resolution", [100, 1000])
def test_target_advances_after_actual_oms_reduction_fill_and_rebuilds(resolution):
    asyncio.run(scenario(resolution, target_reduce=True))


@pytest.mark.parametrize("resolution", [100, 1000])
def test_cancelled_partial_reduction_restores_actual_stop_capacity(resolution):
    asyncio.run(scenario(resolution, target_reduce=True, cancel_reduce=True))


@pytest.mark.parametrize("resolution", [100, 1000])
def test_partially_filled_then_cancelled_reduction_restores_remaining_position_protection(resolution):
    asyncio.run(scenario(resolution, target_reduce=True, cancel_reduce=True, partial_cancel=True))


@pytest.mark.parametrize("failure", ["modify", "submit", "cancel", "lost_response"])
def test_failed_partial_reduction_restores_broker_confirmed_stop_capacity(failure):
    asyncio.run(scenario(1000, target_reduce=True, allocation_failure=failure))


@pytest.mark.parametrize("resolution", [100, 1000])
def test_add_then_partial_reduction_preserves_exact_surviving_lot_protection(resolution):
    asyncio.run(scenario(resolution, target_reduce=True, add_before_reduce=True))


@pytest.mark.parametrize("invalid", ["cash", "assignment", "future", "deadline", "profile"])
def test_invalid_proposals_do_not_mutate_journal_or_broker(invalid):
    asyncio.run(scenario(1000, invalid))


def test_portfolio_rejects_request_above_actual_cash_under_its_policy():
    asyncio.run(scenario(1000, initial_cash=100.))


@pytest.mark.parametrize("resolution", [100, 1000])
def test_actual_protective_fill_closes_actor_position_with_fees(resolution):
    asyncio.run(scenario(resolution, stop_exit=True))


@pytest.mark.parametrize('risk_limit', [100., 120.])
def test_program_risk_rejects_at_proposal_or_current_approved_quote_without_orders(risk_limit):
    asyncio.run(scenario(100, risk_limit=risk_limit))


def test_program_risk_compatible_request_uses_normal_oms_and_broker():
    asyncio.run(scenario(100, risk_limit=300.))


@pytest.mark.parametrize('mode', [RunMode.REPLAY, RunMode.PAPER, RunMode.LIVE])
def test_program_mode_label_cannot_bypass_native_execution_receipts(mode):
    asyncio.run(scenario(100, unsupported_mode=mode))
