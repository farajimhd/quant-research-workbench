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
    if (selector is None or not isinstance(journal_scope, Mapping) or not isinstance(signal_scope, Mapping)
            or type(scopes) is not tuple or not scopes
            or any(not isinstance(scope, Mapping) for scope in scopes)
            or not callable(checkpoint_connection_factory)
            or type(checkpoint_concurrency) is not int or not 1 <= checkpoint_concurrency <= 8):
        raise ValueError("App programme preparation lacks its explicit native authorities")
    journal_scope, signal_scope = dict(journal_scope), dict(signal_scope)
    signature(attach_program_journal).bind(controller, **journal_scope)
    if any(scope.get("source_publisher") is not signal_publisher for scope in scopes):
        raise ValueError("App preparation differs from its cohort Signal Stream publisher")
    await selector._review_controller(run_service, controller)
    if controller._runtime is not None or controller._journal is not None:
        raise ValueError("App programme preparation requires its fresh unadopted controller")
    try:
        # Complete input proof and read preparation precede publication.
        work = await prepare_program_session_input_plan(controller, cohorts, scopes=scopes, history_post=history_post)
        await controller.attach_prepared_program_journal(**journal_scope)
        await controller._initialize_runtime(record_configuration=False, record_lifecycle=False)
        await controller.bootstrap_program_signals(signal_publisher, **signal_scope)
        await bind_prepared_program_launch(controller, work,
            checkpoint_connection_factory=checkpoint_connection_factory,
            checkpoint_concurrency=checkpoint_concurrency)
        return await selector.stage_controller(run_service, controller)
    except BaseException as exc:
        controller._program_launch_plan = None
        controller._program_feature_cache = None
        if controller._journal is not None or controller._runtime is not None:
            controller._program_dispatch_failure = exc
            await _settle_owned_task(asyncio.create_task(controller._close_fixed_journal()))
        raise
