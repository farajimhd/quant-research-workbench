"""Programme terminal publication through the existing V4 broker/account lane."""
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class ProgramTerminalResult:
    status: str
    last_sequence: int
    last_batch_id: str
    source_cursor: str


async def finish_prepared_program_session(controller, *, status):
    """Finish the native runtime and acknowledge its lifecycle-last receipt.

    No market event or simulated fill is manufactured during close. The caller
    remains responsible for releasing its writer, leases and owned connections.
    """
    from .replay_run_service import ReplayRunController
    from .backtest_typed_publisher import BacktestTypedJournalPublisher, TypedBacktestReceipt
    from .backtest_journal_memory import BacktestMemoryJournal
    from .backtest_program_journal_startup import _settle_owned_task
    if (type(controller) is not ReplayRunController
            or status not in ("completed", "stopped", "failed")
            or getattr(controller, "_program_terminal_active", False)
            or getattr(controller, "_program_session_active", False)
            or getattr(controller, "_program_cohort_active", False)
            or getattr(controller, "_program_checkpoint_active", False)):
        raise RuntimeError("Programme terminal lacks exclusive native ownership")
    runtime, actor = controller._runtime, controller._program_strategy
    publisher, journal = controller._journal_publisher, controller._journal
    source, definition = controller._program_signal_runtime, controller.definition
    evaluated = getattr(controller, "_program_evaluated_terminal", None)
    if (runtime is None or actor is None or runtime.strategy is not actor
            or runtime.journal is not journal or controller._runtime_finished
            or type(journal) is not BacktestMemoryJournal
            or type(publisher) is not BacktestTypedJournalPublisher
            or publisher.journal is not journal or not publisher.exact_oms_prices
            or publisher.writer.journal_profile != "backtest_v4"
            or publisher._error is not None or publisher._terminal_task is not None
            or publisher._task is not None and not publisher._task.done()
            or controller._program_dispatch_failure is not None
            or journal.unfenced_records() or publisher.fenced_sequence != journal.latest_sequence(controller.run_id)
            or source is None or runtime.last_event_time is None
            or runtime.last_event_time != controller.current_time
            or not publisher._source_cursor
            or status == "completed" and runtime.last_event_time != definition.session_end
            or set(runtime.config.account_ids) != set(controller.account_ids)
            or not controller.account_ids):
        raise RuntimeError("Programme terminal requires its acknowledged running boundary")
    if status == "completed" and (not isinstance(evaluated, tuple) or len(evaluated) != 5
            or evaluated[0] is not definition or evaluated[1] is not runtime or evaluated[2] is not actor
            or evaluated[3:] != (controller.current_time, publisher.fenced_sequence)):
        raise RuntimeError("Programme completion requires its evaluated terminal session")
    owners = (definition, runtime, actor, publisher, journal, source, runtime.broker,
        runtime.portfolio, runtime.order_manager, runtime.config, publisher.writer)
    clock = runtime.last_event_time
    cursor = source.typed_source_cursor()
    controller._program_terminal_active = True
    controller._program_terminal_task = asyncio.current_task()
    started = False
    try:
        started = True
        # Shield and settle runtime close as well as publication: cancellation
        # cannot leave its broker tasks closing while another owner dispatches.
        job = asyncio.create_task(runtime.finish(status=status))
        try:
            await asyncio.shield(job)
        except asyncio.CancelledError:
            await _settle_owned_task(job)
            raise
        controller._runtime_finished = True
        current = (controller.definition, controller._runtime, controller._program_strategy,
            controller._journal_publisher, controller._journal, controller._program_signal_runtime,
            runtime.broker, runtime.portfolio, runtime.order_manager, runtime.config, publisher.writer)
        if (any(before is not after for before, after in zip(owners, current))
                or runtime.last_event_time != clock or source.typed_source_cursor() != cursor):
            raise RuntimeError("Programme terminal ownership changed during runtime close")
        records = journal.unfenced_records()
        terminal = records[-1] if records else None
        if (terminal is None or (terminal.category, terminal.entity_type, terminal.entity_id)
                != ("lifecycle", "run", controller.run_id)
                or terminal.payload.get("status") != status or terminal.event_time != clock):
            raise RuntimeError("Programme terminal lifecycle is not last at its completed clock")
        captures = tuple(runtime.portfolio.capture_recovery_snapshot(account_id,
            state_revision=terminal.sequence, snapshot_at=clock)
            for account_id in sorted(controller.account_ids))
        task = publisher.enqueue_terminal(captures)
        try:
            receipt = await asyncio.shield(task)
        except asyncio.CancelledError:
            await _settle_owned_task(task)
            raise
        if (type(receipt) is not TypedBacktestReceipt or receipt.last_sequence != terminal.sequence
                or publisher.fenced_sequence != terminal.sequence or journal.unfenced_records()
                or receipt.last_batch_id != publisher._batch_id
                or receipt.source_cursor != publisher._source_cursor or publisher._error is not None):
            raise RuntimeError("Programme terminal receipt differs from its lifecycle fence")
        current = (controller.definition, controller._runtime, controller._program_strategy,
            controller._journal_publisher, controller._journal, controller._program_signal_runtime,
            runtime.broker, runtime.portfolio, runtime.order_manager, runtime.config, publisher.writer)
        if (any(before is not after for before, after in zip(owners, current))
                or runtime.last_event_time != clock or source.typed_source_cursor() != cursor):
            raise RuntimeError("Programme terminal ownership changed during publication")
        controller.status = status
        controller.updated_at = datetime.now(timezone.utc)
        return ProgramTerminalResult(status, receipt.last_sequence, receipt.last_batch_id, receipt.source_cursor)
    except BaseException as exc:
        if started:
            controller._program_dispatch_failure = exc
        raise
    finally:
        controller._program_terminal_active = False
        controller._program_terminal_task = None
