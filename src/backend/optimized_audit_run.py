"""Operational composition of the existing native 100ms app Backtest lane.

Calling this module's runner starts the explicitly supplied approved handoff.
It installs no app server, strategy, market producer, broker, or schema.
"""
import asyncio
from dataclasses import dataclass
from math import isfinite
from typing import Callable

from .program_app_handoff import ProgramAppHandoff
from .program_configuration_selection_service import (
    ProgramConfigurationSelectionService, program_configuration_selection_service,
)
from .replay_run_service import ReplayRunService, ReplayRunController, TERMINAL_REPLAY_STATUSES
from .optimized_execution_contract import verify_optimized_execution_contract
from .optimized_audit_collection import collect_native_execution_audit
from .recorded_program_comparison import verify_frozen_program_release, compare_recorded_program_sessions
from .program_policy_recovery import RecoveredProgramPolicy, program_policy_fingerprint
from .backtest_program_inputs import PreparedProgramBlock
from .signal_stream_runtime_service import SignalStreamRuntime
from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
from src.trading_runtime.optimized_strategy_reference import OptimizationReference


@dataclass(frozen=True)
class NativeAuditContext:
    """Completed native prefix and independently recovered comparison inputs."""
    prefix: V4CommittedPrefix
    packet: RecoveredProgramPolicy
    blocks: tuple[PreparedProgramBlock, ...]
    source: SignalStreamRuntime


@dataclass(frozen=True)
class NativeAuditRunBounds:
    preparation_seconds: float = 600.
    run_seconds: float = 36000.
    evidence_seconds: float = 600.
    cleanup_seconds: float = 120.
    financial_page_size: int = 500
    maximum_financial_rows: int = 100000
    decision_page_size: int = 200
    maximum_decisions: int = 1000000
    maximum_observation_children: int = 1000000
    maximum_measure_rows: int = 2000000
    maximum_audit_rows: int = 6000000

    def __post_init__(self):
        for name in ('preparation_seconds', 'run_seconds', 'evidence_seconds', 'cleanup_seconds'):
            value = getattr(self, name)
            if type(value) not in (int, float) or not isfinite(value) or value <= 0:
                raise ValueError('Native audit phase timeouts must be finite and positive')
        for name in self.__dataclass_fields__:
            if name.endswith('_seconds'):
                continue
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError('Native audit row/page bounds must be explicit positive integers')
        if (self.financial_page_size > 1000 or self.decision_page_size > 500
                or self.maximum_financial_rows > 1000000 or self.maximum_decisions > 1000000
                or self.maximum_observation_children > 1000000):
            raise ValueError('Native audit bounds exceed the existing reader contracts')

    def collection_arguments(self):
        return {name: getattr(self, name) for name in self.__dataclass_fields__ if not name.endswith('_seconds')}


class NativeAuditCleanupUncertain(RuntimeError):
    def __init__(self, run_id, task_name):
        self.run_id, self.task_name = run_id, task_name
        super().__init__(f'Native run {run_id} did not settle within cleanup bound; '
                         f'task {task_name} remains registered with ReplayRunService and requires follow-up')


@dataclass(frozen=True)
class NativeAuditRunResult:
    run_id: str
    day: str
    audit: object
    comparisons: tuple[dict, ...]
    execution_compatibility: object


def validate_native_audit_preparation(controller, reference):
    """Require real prepared native contracts and unchanged frozen sizing/risk."""
    if (type(controller) is not ReplayRunController or type(reference) is not OptimizationReference
            or reference.candidate_envelope is None or reference.clock_windows is None
            or controller._program_strategy is None or controller._runtime is None
            or controller._program_liquidity_resolution_ms != 100
            or controller.definition.execution_interval != '100ms'):
        raise ValueError('Native candidate audit requires prepared 100ms app ownership and frozen candidate clocks')
    definition = controller.definition
    day = definition.session_date.isoformat()
    if (day not in reference.sessions or day not in reference.clock_windows
            or definition.requested_start != reference.clock_windows[day].requested_start
            or definition.session_end != reference.clock_windows[day].last_clock):
        raise ValueError('Native audit definition differs from the complete frozen session window')
    actor = controller._program_strategy
    verify_frozen_program_release(reference, actor.program_release)
    plan = controller._program_launch_plan
    broker = getattr(controller, '_program_broker_contract', None)
    if plan is None or broker is None:
        raise ValueError('Native audit lacks its bound native launch and broker contracts')
    # The execution binding is retained on each fully prepared cohort scope.
    work = plan.cohorts
    bindings = [cohort.scope.get('execution_policy') for cohort in work]
    if not bindings or any(binding is None for binding in bindings):
        raise ValueError('Native audit lacks approved cohort execution binding')
    hashes = {binding.header['content_hash'] for binding in bindings}
    if len(hashes) != 1:
        raise ValueError('Native audit cohorts disagree on selected execution binding')
    compatibility = verify_optimized_execution_contract(reference.execution, mode='backtest',
        configuration_revision=definition.configuration_revision, execution_binding=bindings[0],
        broker_config=controller._runtime.broker.config,
        liquidity_plan_hash=broker.row['liquidity_plan_hash'], execution_risk=actor.execution_risk)
    compatibility.require_compatible()
    return day, compatibility


def _validate_context(context, controller):
    if (type(context) is not NativeAuditContext or type(context.prefix) is not V4CommittedPrefix
            or context.prefix.status != 'completed' or context.prefix.run_id != controller.run_id
            or type(context.packet) is not RecoveredProgramPolicy
            or context.packet.run_id != controller.run_id
            or context.packet.journal_sequence != context.prefix.last_sequence
            or type(context.blocks) is not tuple or not context.blocks
            or any(type(block) is not PreparedProgramBlock for block in context.blocks)
            or type(context.source) is not SignalStreamRuntime
            or context.source.typed_source_cursor() != controller._program_signal_runtime.typed_source_cursor()
            or context.source.typed_source_commit_hashes() != controller._program_signal_runtime.typed_source_commit_hashes()
            or context.packet.state_hash != program_policy_fingerprint(controller._program_strategy)):
        raise ValueError('Completed audit context differs from the actual native run and recovered state')


async def _settle_run(controller, timeout):
    task = controller._task
    if task is None:
        return
    try:
        async with asyncio.timeout(timeout):
            if not task.done() and controller.status not in TERMINAL_REPLAY_STATUSES:
                await controller.command('stop')
            await asyncio.shield(task)
    except TimeoutError as error:
        raise NativeAuditCleanupUncertain(controller.run_id, task.get_name()) from error


async def run_native_candidate_audit(*, run_service, selector, handoff, reference,
                                     evidence_factory: Callable, reader_factory: Callable,
                                     bounds=NativeAuditRunBounds()):
    """Start one explicit native handoff, await completion, then audit its prefix.

    evidence_factory is an async caller-owned cold-recovery composition receiving
    the completed controller and returning NativeAuditContext. The caller keeps
    prepared block/source receipts needed by that recovery. Source publishers and
    installed app selection remain caller-owned. No reference data enters replay.
    """
    if (type(run_service) is not ReplayRunService
            or type(selector) is not ProgramConfigurationSelectionService
            or program_configuration_selection_service() is not selector
            or type(handoff) is not ProgramAppHandoff or type(bounds) is not NativeAuditRunBounds
            or type(reference) is not OptimizationReference or reference.candidate_envelope is None
            or handoff.liquidity_resolution_ms != 100 or handoff.definition.execution_interval != '100ms'
            or not callable(evidence_factory) or not callable(reader_factory)):
        raise ValueError('Native audit requires installed approved app owners and explicit 100ms handoff')
    controller = None
    launched = False
    try:
        controller = await asyncio.wait_for(selector.prepare_app_controller(run_service,
            handoff.definition, **handoff.arguments()), bounds.preparation_seconds)
        day, compatibility = validate_native_audit_preparation(controller, reference)
        await asyncio.wait_for(selector.launch_controller(run_service, controller), bounds.preparation_seconds)
        launched = True
        if controller._task is None:
            raise RuntimeError('Native service launch did not retain its owned task')
        await asyncio.wait_for(asyncio.shield(controller._task), bounds.run_seconds)
        if controller.status != 'completed' or controller._program_dispatch_failure is not None:
            raise RuntimeError(f'Native audit run {controller.run_id} finished with status {controller.status}')
        context = await asyncio.wait_for(evidence_factory(controller), bounds.evidence_seconds)
        _validate_context(context, controller)
        audit = await asyncio.wait_for(collect_native_execution_audit(reader_factory,
            context.prefix, context.packet, context.blocks, context.source, reference,
            day=day, **bounds.collection_arguments()), bounds.evidence_seconds)
        comparisons = compare_recorded_program_sessions(reference, {day: audit['recorded_metrics']})
        return NativeAuditRunResult(controller.run_id, day, audit, tuple(comparisons), compatibility)
    except BaseException as original:
        try:
            if controller is not None and controller._task is not None:
                await _settle_run(controller, bounds.cleanup_seconds)
            elif controller is not None and not launched:
                # Native disposal also closes this one-off selector. It must be
                # reinstalled explicitly before any subsequent preparation.
                await asyncio.wait_for(selector.close_prepared_handoff(), bounds.cleanup_seconds)
        except BaseException as cleanup:
            raise BaseExceptionGroup('Native candidate audit failed and cleanup requires attention',
                                     [original, cleanup])
        raise
