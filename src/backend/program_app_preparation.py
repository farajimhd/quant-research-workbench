"""Compose existing programme preparation into the app's native handoff."""
import asyncio
from collections.abc import Mapping
from inspect import signature

from .program_configuration_selection_service import program_configuration_selection_service
from .backtest_program_input_plan import prepare_program_session_input_plan
from .backtest_program_journal_startup import attach_program_journal, _settle_owned_task
from .backtest_program_launch import bind_prepared_program_launch


async def complete_program_app_preparation(run_service, controller, *, cohorts, scopes,
        journal_scope, signal_publisher, signal_scope, checkpoint_connection_factory,
        checkpoint_concurrency, history_post=None):
    """Prepare and stage, never admit or start the backtest task.

    Operator authorization is required for the existing typed journal/source
    publication steps. This installs no schema and invokes no market producer.
    Caller-owned bootstrap clients and Signal Stream publisher stay caller-owned.
    """
    selector = program_configuration_selection_service()
    from .program_managed_journal import (ManagedProgramJournalRequest, ManagedProgramJournalScope,
        prepare_managed_journal_contracts, managed_program_journal_scope)
    managed = type(journal_scope) is ManagedProgramJournalRequest
    if (selector is None or (not managed and not isinstance(journal_scope, Mapping)) or not isinstance(signal_scope, Mapping)
            or type(scopes) is not tuple or not scopes
            or any(not isinstance(scope, Mapping) for scope in scopes)
            or not callable(checkpoint_connection_factory)
            or type(checkpoint_concurrency) is not int or not 1 <= checkpoint_concurrency <= 8):
        raise ValueError("App programme preparation lacks its explicit native authorities")
    signal_scope = dict(signal_scope)
    if not managed:
        journal_scope = dict(journal_scope)
        signature(attach_program_journal).bind(controller, **journal_scope)
    if any(scope.get("source_publisher") is not signal_publisher for scope in scopes):
        raise ValueError("App preparation differs from its cohort Signal Stream publisher")
    await selector._review_controller(run_service, controller)
    if controller._runtime is not None or controller._journal is not None:
        raise ValueError("App programme preparation requires its fresh unadopted controller")
    from .program_signal_source import (ProgramSignalPublisherFactory, require_program_signal_publisher)
    from .signal_execution_context import SignalExecutionContext
    from .qmd_columnar_history_transport import _owned_worker
    if type(cohorts) is not tuple or not cohorts:
        raise ValueError('Program source binding requires certified native input cohorts')
    context = SignalExecutionContext(controller.run_id, controller.definition.mode.value,
        controller.definition.session_date.isoformat(),
        controller.definition.configuration_revision['revision_id'],
        cohorts[0].population_pin.source_revision)
    created_publisher = False
    contracts = None
    async def adopt_and_stage(scope, work):
        await controller.attach_prepared_program_journal(**scope)
        if type(scope) is ManagedProgramJournalScope:
            scope.mark_adopted()
        await controller._initialize_runtime(record_configuration=False, record_lifecycle=False)
        await controller.bootstrap_program_signals(signal_publisher, **signal_scope)
        await bind_prepared_program_launch(controller, work,
            checkpoint_connection_factory=checkpoint_connection_factory,
            checkpoint_concurrency=checkpoint_concurrency)
        return await selector.stage_controller(run_service, controller)
    try:
        if type(signal_publisher) is ProgramSignalPublisherFactory:
            signal_publisher = await signal_publisher.create(context)
            created_publisher = True
            scopes = tuple({**scope, 'source_publisher': signal_publisher} for scope in scopes)
        require_program_signal_publisher(context, signal_publisher)
        if managed:
            contracts = await _owned_worker(prepare_managed_journal_contracts, controller, journal_scope)
        # Complete input proof and read preparation precede publication.
        work = await prepare_program_session_input_plan(controller, cohorts, scopes=scopes, history_post=history_post)
        if managed:
            async with managed_program_journal_scope(controller, journal_scope, contracts=contracts) as scope:
                return await adopt_and_stage(scope, work)
        return await adopt_and_stage(journal_scope, work)
    except BaseException as exc:
        if managed:
            with selector._prepared_lock:
                staged = selector._prepared
                if staged is not None and staged[0] is run_service and staged[1] is controller:
                    selector._prepared = None
        controller._program_launch_plan = None
        controller._program_feature_cache = None
        controller._program_population_cache = None
        cleanup_errors = []
        if controller._journal is not None or controller._runtime is not None:
            controller._program_dispatch_failure = exc
            async def close_owned():
                errors = []
                if controller._runtime is not None:
                    try:
                        await controller._runtime.close_background_tasks()
                    except BaseException as error:
                        errors.append(error)
                try:
                    await controller._close_fixed_journal()
                except BaseException as error:
                    errors.append(error)
                if errors:
                    raise BaseExceptionGroup("Programme preparation cleanup failed", errors)
            try:
                await _settle_owned_task(asyncio.create_task(close_owned()))
            except BaseException as cleanup_error:
                cleanup_errors.append(cleanup_error)
        if created_publisher:
            try:
                await _owned_worker(signal_publisher.close)
            except BaseException as cleanup_error:
                cleanup_errors.append(cleanup_error)
        if cleanup_errors:
            raise BaseExceptionGroup('Programme preparation and cleanup failed', [exc, *cleanup_errors])

        raise


async def complete_recovered_program_app_preparation(run_service, controller, *,
        evidence, cohorts, scopes, journal_scope, source, packet, population,
        oms_image, broker_image, intent_planner, keeper_session, lease,
        checkpoint_connection_factory, checkpoint_concurrency, history_post=None):
    """Compose the existing cold readers/runtime/admission preparation path.

    Operational authorization remains required. This opens only the supplied
    resumed writer lane; no new context, producer, schema or run task starts.
    Caller-owned readers, source publisher and unadopted leases remain owned by
    the caller. Successful adoption transfers the writer/runtime/lease/session.
    """
    from .backtest_program_journal_bootstrap import assemble_resumed_program_v4_journal
    from .program_runtime_recovery import build_recovered_program_runtime
    from .backtest_v4_run_context import historical_strategy_one_portfolio_profiles
    from .backtest_program_journal_startup import _close_unadopted_assembly
    selector = program_configuration_selection_service()
    if (selector is None or not isinstance(journal_scope, Mapping)
            or not callable(checkpoint_connection_factory) or type(checkpoint_concurrency) is not int
            or not 1 <= checkpoint_concurrency <= 8):
        raise ValueError("Recovered app preparation lacks explicit native authorities")
    journal_scope = dict(journal_scope)
    signature(assemble_resumed_program_v4_journal).bind(**journal_scope)
    if (journal_scope["recovery_evidence"] is not evidence
            or journal_scope["release"] is not controller._program_strategy.program_release):
        raise ValueError("Recovered app preparation differs from its joined evidence")
    from .signal_execution_context import SignalExecutionContext
    expected_context = SignalExecutionContext(controller.run_id, controller.definition.mode.value,
        controller.definition.session_date.isoformat(),
        controller.definition.configuration_revision['revision_id'], population.source_revision)
    if getattr(source, 'source_context', None) != expected_context:
        raise ValueError('Recovered programme source differs from its exact native execution context')
    await selector._review_controller(run_service, controller)
    if controller._runtime is not None or controller._journal is not None:
        raise ValueError("Recovered app preparation requires its unadopted controller")
    _, groups = historical_strategy_one_portfolio_profiles(controller.definition.configuration_revision["payload"])
    job, result = None, None
    def build():
        assembly = None
        try:
            assembly, anchor = assemble_resumed_program_v4_journal(**journal_scope)
            runtime = build_recovered_program_runtime(journal_scope["runtime_config"], controller._program_strategy,
                assembly.journal, portfolio_recovery=evidence.financial.portfolio,
                oms_image=oms_image, broker_image=broker_image,
                simulation_config=journal_scope["broker_contract"].config,
                completed_at=evidence.financial.completed_at, intent_planner=intent_planner,
                strategy_configuration=controller.definition.configuration_revision["payload"]["strategy"], groups=groups)
            return assembly, anchor, runtime
        except BaseException as exc:
            if assembly is not None:
                errors = []
                for owned in (assembly.writer, assembly.journal):
                    try: owned.close()
                    except BaseException as error: errors.append(error)
                if errors:
                    raise BaseExceptionGroup("Recovered runtime build and cleanup failed", [exc, *errors])
            raise
    try:
        work = await prepare_program_session_input_plan(controller, cohorts, scopes=scopes, history_post=history_post)
        job = asyncio.create_task(asyncio.to_thread(build))
        result = await asyncio.shield(job)
        assembly, anchor, runtime = result
        await runtime.initialize(record_lifecycle=False)
        await controller.adopt_program_cold_runtime(assembly=assembly, evidence=evidence, runtime=runtime,
            packet=packet, source=source, population=population, oms_image=oms_image, anchor=anchor,
            lease=lease, keeper_session=keeper_session, broker_contract=journal_scope["broker_contract"],
            policy_blocks=tuple({id(block): block for unit in work for block in unit.blocks.values()}.values()))
        await controller.bind_recovered_program_session(work,
            checkpoint_connection_factory=checkpoint_connection_factory, checkpoint_concurrency=checkpoint_concurrency)
        return await selector.stage_controller(run_service, controller)
    except BaseException as exc:
        if result is None and job is not None:
            try: result = await _settle_owned_task(job)
            except BaseException as worker_error:
                # The worker closes any partial assembly itself. Preserve its
                # failure as well as cancellation of the waiting caller.
                if worker_error is not exc:
                    raise BaseExceptionGroup("Recovered preparation and worker failed", [exc, worker_error])
        controller._program_launch_plan = None
        controller._program_feature_cache = None
        controller._program_population_cache = None
        if result is not None:
            assembly, _, runtime = result
            async def close():
                errors = []
                try: await runtime.close_background_tasks()
                except BaseException as error: errors.append(error)
                try:
                    if controller._runtime is runtime:
                        controller._program_dispatch_failure = exc
                        await controller._close_fixed_journal()
                    else:
                        await _close_unadopted_assembly(assembly)
                except BaseException as error: errors.append(error)
                if errors: raise BaseExceptionGroup("Recovered preparation cleanup failed", errors)
            try: await _settle_owned_task(asyncio.create_task(close()))
            except BaseException as error:
                raise BaseExceptionGroup("Recovered preparation and cleanup failed", [exc, error])
        raise
