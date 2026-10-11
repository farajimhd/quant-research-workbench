"""Main app controller constructs the shared program/Portfolio/OMS runtime."""
import asyncio
from dataclasses import replace
from datetime import date, time, datetime, UTC, timedelta

import pytest

from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.backend.backtest_v4_run_context import historical_simulated_account_ids
from src.backend.replay_run_service import ReplayRunController, ReplayRunDefinition, _backtest_launch_blocker
from src.trading_runtime.columnar_program_actor import AssignedProgramStrategy
from src.trading_runtime.runtime import RunMode
from src.trading_runtime.execution_policies import ExecutionPolicy, ExecutionEnvelope, ProtectionProfile, ProtectionSlice, StopRule
from src.trading_runtime.strategy_engine import StrategyAssignment, StrategyPermissions, AssignmentStatus
from tests.test_columnar_program_coordinator import inputs
from tests.test_replay_run_service import approved_configuration
from tests.test_broker_liquidity_resolution import second_bar


def definition_and_actor():
    release, _, _, _ = inputs(entry=True)
    configuration = approved_configuration()
    configuration["payload"]["strategy"] = dict(strategy_id=release.header["strategy_id"],
        revision=release.header["strategy_number"], parameters={})
    account, = historical_simulated_account_ids(mode=RunMode.BACKTEST,
        configuration=configuration["payload"])
    assignment = StrategyAssignment("assignment", release.header["strategy_id"],
        release.header["strategy_number"], account, "ABC", 1,
        AssignmentStatus.WATCHING, StrategyPermissions(enter=True, add=True), {})
    actor = AssignedProgramStrategy(release, (assignment,), {"assignment": "listing"})
    definition = ReplayRunDefinition(session_date=date(2026, 10, 12), start_time=time(4),
        mode=RunMode.BACKTEST, configuration_revision=configuration, execution_interval="100ms",
        market_data_plan=dict(token="synthetic-plan", execution_interval=dict(milliseconds=100)))
    return definition, actor


@pytest.mark.parametrize("resolution", [100, 1000])
def test_controller_builds_program_actor_on_existing_runtime_without_legacy_factory(tmp_path, resolution):
    definition, actor = definition_and_actor()
    controller = ReplayRunController(definition, runtime_root=tmp_path, program_strategy=actor,
        program_liquidity_resolution_ms=resolution)
    controller._journal = BacktestMemoryJournal(run_id=controller.run_id)
    async def run():
        await controller._initialize_runtime(record_configuration=False, record_lifecycle=False)
        try:
            assert controller._strategy is actor
            assert controller._runtime.strategy is actor
            assert controller._strategy_registration is None
            assert controller._runtime.order_manager is not None
            assert controller._runtime.portfolio is not None
            assert controller._runtime.broker.config.commission_per_share == .005
            assert controller._runtime.broker.config.minimum_commission == 1.
            assert controller._runtime.broker.config.market_slippage_bps == 5.
            assert controller._runtime.broker.config.liquidity_resolution_ms == resolution
            assert actor.assignments()[0].account_id in controller.account_ids
            assert not actor.execution_state.positions
            # Construction does not admit an unqualified operational run.
            assert _backtest_launch_blocker(definition)
            at = datetime(2026, 10, 12, 14, tzinfo=UTC)
            _, boundary, _, management = inputs(entry=True)
            boundary = replace(boundary, boundary_at=at, available_at=at)
            management = replace(management, observed_at=at)
            row = second_bar(at)
            row.update(ticker="ABC", ask_int=105000, bid_int=104900, close_int=105000,
                high_int=105100, low_int=104800)
            if resolution == 100:
                row.update(resolution_ms=100, bucket_index=(row["bucket_index"] + 1) * 10 - 1)
            runtime = controller._runtime
            await runtime.process_liquidity_boundary((row,), at=at)
            proposal, = actor.proposals(boundary, assignment_id="assignment", management=management)
            result = await runtime.submit_program_proposal(proposal,
                account_id=actor.assignments()[0].account_id, assignment_id="assignment",
                execution_policy=ExecutionPolicy(envelope=ExecutionEnvelope(deadline_ms=2000)),
                protection_profile=ProtectionProfile("test", 1,
                    (ProtectionSlice("one", 1., StopRule(price=9.)),)), outside_rth=False)
            assert result[0]["decision"]["status"] in {"approved", "resized"}
            assert not actor.position_for("assignment").position_open
            at += timedelta(seconds=1)
            row.update(last_event_us=int(at.timestamp() * 1_000_000) - 1,
                quote_timestamp_us=int(at.timestamp() * 1_000_000) - 10001,
                bucket_index=row["bucket_index"] + 1000 // resolution)
            await runtime.process_liquidity_boundary((row,), at=at)
            position = actor.position_for("assignment")
            assert position.position_open and position.fees > 0
            assert float(position.quantity) == runtime.broker.position_quantity(
                actor.assignments()[0].account_id, 1, "ABC")
        finally:
            await controller._runtime.finish()
    asyncio.run(run())


@pytest.mark.parametrize("change", [dict(resume_state={}), dict(fixed_v4_runtime_image=object())])
def test_program_binding_rejects_other_recovery_authorities_before_runtime(tmp_path, change):
    definition, actor = definition_and_actor()
    with pytest.raises(ValueError, match="fresh actor"):
        ReplayRunController(definition, runtime_root=tmp_path, program_strategy=actor,
            program_liquidity_resolution_ms=100, **change)


def test_program_controller_rejects_untyped_journal_and_account_substitution(tmp_path):
    definition, actor = definition_and_actor()
    controller = ReplayRunController(definition, runtime_root=tmp_path, program_strategy=actor,
        program_liquidity_resolution_ms=100)
    with pytest.raises(RuntimeError, match="normalized Backtest journal"):
        asyncio.run(controller._initialize_runtime(record_configuration=False, record_lifecycle=False))
    assignment = replace(actor.assignments()[0], account_id="other-account")
    other = AssignedProgramStrategy(actor.program_release, (assignment,), {"assignment": "listing"})
    controller = ReplayRunController(definition, runtime_root=tmp_path, program_strategy=other,
        program_liquidity_resolution_ms=100)
    controller._journal = BacktestMemoryJournal(run_id=controller.run_id)
    with pytest.raises(ValueError, match="resolved Backtest accounts"):
        asyncio.run(controller._initialize_runtime(record_configuration=False, record_lifecycle=False))


@pytest.mark.parametrize("resolution", [None, True, 500])
def test_program_liquidity_requires_explicit_supported_selection(tmp_path, resolution):
    definition, actor = definition_and_actor()
    with pytest.raises(ValueError, match="fresh actor"):
        ReplayRunController(definition, runtime_root=tmp_path, program_strategy=actor,
            program_liquidity_resolution_ms=resolution)
