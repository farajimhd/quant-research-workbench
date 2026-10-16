"""Source-only admission tests; no DB-backed native run is simulated as qualified."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

from src.backend.optimized_audit_run import (
    NativeAuditRunBounds, validate_native_audit_preparation, run_native_candidate_audit,
)
from src.backend.replay_run_service import ReplayRunController, ReplayRunService
from src.trading_runtime.optimized_strategy_reference import OptimizationReference, ReferenceClockWindow
from src.trading_runtime.columnar_program_actor import AssignedProgramStrategy
from src.trading_runtime.configuration_execution_risk import bind_execution_risk
from src.trading_runtime.simulated_broker import SimulationConfig
from tests.test_program_controller_runtime import definition_and_actor
from tests.test_optimized_strategy_import import prepared
from tests.test_configuration_execution_risk import contract
from tests.test_optimized_execution_contract import inputs


def source_admission_fixture(tmp_path):
    imported = prepared()
    definition, prior_actor = definition_and_actor()
    _, selected = inputs()
    risk = contract()
    revision = bind_execution_risk(selected['configuration_revision'], risk)
    revision['payload']['strategy'] = dict(definition.configuration_revision['payload']['strategy'],
        strategy_id=imported.release.header['strategy_id'], revision=imported.release.header['strategy_number'])
    definition = replace(definition, execution_interval='100ms', configuration_revision=revision)
    assignments = tuple(replace(row, strategy_id=imported.release.header['strategy_id'],
        strategy_revision=imported.release.header['strategy_number']) for row in prior_actor.assignments())
    actor = AssignedProgramStrategy(imported.release, assignments, prior_actor._listings, execution_risk=risk)
    controller = ReplayRunController(definition, runtime_root=tmp_path, program_strategy=actor,
        program_liquidity_resolution_ms=100)
    # Only local static validation is under test. These wrappers are not native
    # launch proofs; native launch still independently requires its real owner gate.
    controller._runtime = SimpleNamespace(broker=SimpleNamespace(config=SimulationConfig()))
    controller._program_launch_plan = SimpleNamespace(cohorts=(SimpleNamespace(scope={
        'execution_policy': selected['execution_binding']}),))
    controller._program_broker_contract = SimpleNamespace(row={'liquidity_plan_hash': 'b' * 64})
    clock = ReferenceClockWindow(definition.requested_start, definition.requested_start,
        definition.session_end, 10, 'a' * 64, 'b' * 64)
    candidate = imported.candidate
    day = definition.session_date.isoformat()
    reference = OptimizationReference(candidate.generation, candidate.program_hash, candidate.candidate,
        candidate.program, candidate.execution, {day: {}}, {day: clock}, candidate)
    return controller, reference


def test_static_native_admission_checks_same_candidate_100ms_and_protected_costs(tmp_path):
    controller, reference = source_admission_fixture(tmp_path)
    day, report = validate_native_audit_preparation(controller, reference)
    assert day == controller.definition.session_date.isoformat()
    assert report.compatible
    controller._runtime.broker.config = replace(SimulationConfig(), commission_per_share=.02)
    with pytest.raises(ValueError, match='fee_per_share'):
        validate_native_audit_preparation(controller, reference)


def test_native_audit_retains_actual_app_baseline_fill_slippage(tmp_path):
    from src.backend.replay_run_service import _simulation_config
    controller, reference = source_admission_fixture(tmp_path)
    actual = _simulation_config(controller.definition)
    assert actual.market_slippage_bps == 5.
    controller._runtime.broker.config = actual
    _, report = validate_native_audit_preparation(controller, reference)
    assert report.compatible
    assert report.modeled_notional_cost_bps == 0.
    assert report.native_market_slippage_bps == 5.
    assert controller._runtime.broker.config is actual


def test_native_admission_rejects_unprotected_or_different_source_release(tmp_path):
    controller, reference = source_admission_fixture(tmp_path)
    controller._program_strategy.execution_risk = None
    with pytest.raises(ValueError, match='stop-risk'):
        validate_native_audit_preparation(controller, reference)
    controller, reference = source_admission_fixture(tmp_path)
    controller._program_liquidity_resolution_ms = 1000
    with pytest.raises(ValueError, match='100ms'):
        validate_native_audit_preparation(controller, reference)
    controller, reference = source_admission_fixture(tmp_path)
    controller._program_strategy.program_release = replace(controller._program_strategy.program_release,
        header=dict(controller._program_strategy.program_release.header, entry_cash=999))
    with pytest.raises(ValueError, match='frozen optimized candidate'):
        validate_native_audit_preparation(controller, reference)


@pytest.mark.parametrize('change', [dict(run_seconds=float('inf')), dict(cleanup_seconds=0),
    dict(maximum_decisions=True), dict(financial_page_size=1001), dict(maximum_financial_rows=1000001)])
def test_bounds_fail_before_operational_work(change):
    with pytest.raises(ValueError): NativeAuditRunBounds(**change)


def test_runner_rejects_uninstalled_owners_without_preparing_or_starting(tmp_path):
    controller, reference = source_admission_fixture(tmp_path)
    service = ReplayRunService(runtime_root=tmp_path)
    with pytest.raises(ValueError, match='installed approved'):
        asyncio.run(run_native_candidate_audit(run_service=service, selector=None,
            handoff=None, reference=reference, evidence_factory=lambda controller: None,
            reader_factory=lambda: None))
    assert not service._runs and controller._task is None


def test_stop_helper_drains_owned_task_and_reports_uncertainty_without_cancelling_it():
    from src.backend.optimized_audit_run import _settle_run, NativeAuditCleanupUncertain
    async def check():
        released = asyncio.Event()
        async def work(): await released.wait()
        task = asyncio.create_task(work(), name='unit-owned-lifecycle-task')
        owner = SimpleNamespace(_task=task, status='running', run_id='unit-run')
        async def stop(command):
            assert command == 'stop'
            owner.status = 'stopped'
            released.set()
        owner.command = stop
        await _settle_run(owner, 1.)
        assert task.done()
        released.clear()
        task = asyncio.create_task(work(), name='unit-stalled-lifecycle-task')
        owner._task = task
        # A terminal status may precede a slow task-finalization drain.
        with pytest.raises(NativeAuditCleanupUncertain, match='unit-run') as error:
            await _settle_run(owner, .001)
        assert error.value.task_name == task.get_name() and not task.cancelled()
        released.set()
        await task
        assert task.done()
    asyncio.run(check())
