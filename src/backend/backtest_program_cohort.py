"""Prepared program scheduling on the app's existing source and filling runtime."""
from dataclasses import dataclass
from collections.abc import Mapping


@dataclass(frozen=True)
class ProgramCohortResult:
    status: str
    liquidity_boundaries: int
    source_clocks: int
    qualified_decisions: int
    policy_packet: object


async def run_fenced_program_cohort(controller, blocks, population, projection, **scope):
    """Retain cohort ownership through verified publication, without terminal close.

    Cancellation does not cancel the shielded publisher. An uncertain fence
    requires recovery before another cohort may use this controller.
    """
    from .backtest_typed_publisher import TypedBacktestReceipt
    from .program_policy_recovery import program_policy_fingerprint
    from .backtest_program_session import require_program_session_owner
    require_program_session_owner(controller)
    if getattr(controller, "_program_cohort_active", False):
        raise RuntimeError("Program cohort already owns this controller")
    if controller._program_dispatch_failure is not None:
        raise RuntimeError("Program cohort requires recovery after an uncertain boundary") from controller._program_dispatch_failure
    controller._program_cohort_active = True
    publishing = False
    try:
        result = await run_prepared_program_cohort(controller, blocks, population, projection, **scope)
        if not result.source_clocks and not result.liquidity_boundaries:
            return result
        publishing = True
        runtime, actor = controller._runtime, controller._program_strategy
        publisher, source = controller._journal_publisher, controller._program_signal_runtime
        journal, writer = runtime.journal, publisher.writer
        owners = (controller.definition, runtime, actor, publisher, source, journal,
            writer, runtime.broker, runtime.config, runtime.order_manager, runtime.portfolio)
        target = journal.latest_sequence(controller.run_id)
        clock, cursor = runtime.last_event_time, source.typed_source_cursor()
        policy_hash = program_policy_fingerprint(actor)
        publisher.enqueue_pending()
        receipt = await publisher.await_fence()
        current = (controller.definition, controller._runtime, controller._program_strategy,
            controller._journal_publisher, controller._program_signal_runtime,
            controller._journal, publisher.writer, runtime.broker, runtime.config,
            runtime.order_manager, runtime.portfolio)
        if (any(before is not after for before, after in zip(owners, current))
                or runtime.journal is not journal or publisher.journal is not journal
                or runtime.last_event_time != clock
                or journal.latest_sequence(controller.run_id) != target
                or source.typed_source_cursor() != cursor
                or program_policy_fingerprint(actor) != policy_hash
                or type(receipt) is not TypedBacktestReceipt
                or receipt.last_sequence != target
                or publisher.fenced_sequence != target
                or receipt.last_batch_id != publisher._batch_id
                or receipt.source_cursor != publisher._source_cursor
                or publisher._error is not None):
            raise RuntimeError("Program cohort publication differs from its owned exact fence")
        return result
    except BaseException as exc:
        if publishing:
            controller._program_dispatch_failure = exc
        raise
    finally:
        controller._program_cohort_active = False


async def run_prepared_program_cohort(controller, blocks, population, projection, *,
        source_publisher, catalogs, reader_factory, first_bucket, stop_bucket, concurrency,
        assignment_order, execution_policy, protection_profiles_at, outside_rth,
        maximum_events, initial_packet=None, market_plan=None, price_plan=None,
        liquidity_certificates=None, terminal=False):
    """Read a bounded certified cohort, then process liquidity before decisions.

    This does not launch a run, manufacture market inputs or commit a terminal
    fence. The admitted main lifecycle owns preparation, batch fences and close.
    """
    from .replay_run_service import ReplayRunController
    from .backtest_program_inputs import PreparedProgramBlock, PreparedProgramScannerProjection, publish_prepared_program_source_clock
    from .qmd_columnar_population import CertifiedPopulationWindow
    from .backtest_market_data import market_day_boundary, SESSION_OPEN_OFFSET_MS
    from .backtest_liquidity_resolution import (CertifiedLiquidityResolution,
        read_liquidity_cohort_async, read_fixed_liquidity_cohort_async)
    from .signal_stream_runtime_service import SignalStreamRuntime
    from .signal_stream_typed_publication import TypedSignalPublicationQueue
    from .backtest_typed_publisher import BacktestTypedJournalPublisher
    from src.trading_runtime.runtime import TradingRuntime, RunMode
    from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter
    from src.trading_runtime.columnar_program_actor import AssignedProgramStrategy
    if (type(controller) is not ReplayRunController or not isinstance(blocks, Mapping)
            or type(population) is not CertifiedPopulationWindow
            or type(projection) is not PreparedProgramScannerProjection
            or type(first_bucket) is not int or type(stop_bucket) is not int
            or not 14400 <= first_bucket < stop_bucket <= 72000 or stop_bucket - first_bucket > 256
            or not callable(protection_profiles_at) or type(outside_rth) is not bool or type(terminal) is not bool
            or isinstance(assignment_order, (str, bytes))
            or type(maximum_events) is not int or not 1 <= maximum_events <= 1_000_000):
        raise ValueError("Program cohort lacks its bounded prepared scope")
    runtime, actor = controller._runtime, controller._program_strategy
    if controller._program_dispatch_failure is not None:
        raise RuntimeError("Program cohort requires recovery after an uncertain boundary") from controller._program_dispatch_failure
    if (type(runtime) is not TradingRuntime or runtime.config.mode is not RunMode.BACKTEST
            or type(runtime.broker) is not SimulatedBrokerAdapter or type(actor) is not AssignedProgramStrategy
            or runtime.strategy is not actor or runtime.journal is not controller._journal
            or projection.release_hash != actor.program_release.header["content_hash"]
            or projection.source_revision != population.source_revision):
        raise ValueError("Program cohort differs from its owned native runtime")
    source, journal_publisher = controller._program_signal_runtime, controller._journal_publisher
    if (type(source) is not SignalStreamRuntime
            or source._typed_scope != (controller.definition.configuration_revision["revision_id"], population.source_revision)
            or type(source_publisher) is not TypedSignalPublicationQueue or source_publisher._closing
            or source_publisher._fatal is not None or type(journal_publisher) is not BacktestTypedJournalPublisher
            or journal_publisher.journal is not runtime.journal or journal_publisher._error is not None
            or journal_publisher._terminal_task is not None
            or journal_publisher._task is not None and not journal_publisher._task.done()):
        raise ValueError("Program cohort lacks its available owned source/journal publishers")
    order = tuple(assignment_order)
    if (not order or len(order) != len(set(order)) or set(order) != set(actor._by_assignment)
            or set(blocks) != set(order) or any(type(block) is not PreparedProgramBlock for block in blocks.values())):
        raise ValueError("Program cohort lacks complete assignment coverage")
    day = controller.definition.session_date.isoformat()
    after = first_bucket * 1000 - SESSION_OPEN_OFFSET_MS
    through = stop_bucket * 1000 - SESSION_OPEN_OFFSET_MS
    clocks = tuple(market_day_boundary(day, boundary) for boundary in range(after + 1000, through + 1, 1000))
    if (runtime.last_event_time is not None and runtime.last_event_time > market_day_boundary(day, after)
            or clocks[0] < controller.definition.requested_start or clocks[-1] > controller.definition.session_end):
        raise ValueError("Program cohort clock window overlaps completed work or exceeds the requested session")
    for at in clocks:
        projection.frame_for(at)
        for identity in order:
            block = blocks[identity]
            assignment = actor._by_assignment[identity]
            if block.release_hash != actor.program_release.header["content_hash"]:
                raise ValueError("Program cohort block differs from its immutable release")
            block.boundary_for(actor.program_release, population, listing_id=actor._listings[identity],
                ticker=assignment.ticker, ordinal=block.ordinal_for(actor._listings[identity], at))
    tickers = {row.ticker for row in actor.assignments()}
    if controller._stop_requested:
        return ProgramCohortResult("stopped", 0, 0, 0, initial_packet)
    resolution = runtime.broker.config.liquidity_resolution_ms
    broker, config, manager, portfolio, journal = (runtime.broker, runtime.config,
        runtime.order_manager, runtime.portfolio, runtime.journal)
    definition = controller.definition
    before_clock = runtime.last_event_time
    before_sequence = journal.latest_sequence(controller.run_id)
    before_source = source.typed_source_cursor()
    before_fence = (journal_publisher.fenced_sequence, journal_publisher._batch_id,
        journal_publisher._source_cursor)
    if resolution == 100:
        if (liquidity_certificates is not None or market_plan is None or price_plan is None
                or set(market_plan.tickers) != tickers or market_plan.sessions != (day,)):
            raise ValueError("Program cohort100ms market coverage differs from assignments")
        rows_by_ticker = await read_fixed_liquidity_cohort_async(reader_factory, market_plan, price_plan,
            after_boundary_ms=after, through_boundary_ms=through, concurrency=concurrency)
    else:
        if (resolution != 1000 or market_plan is not None or price_plan is not None
                or not isinstance(liquidity_certificates, (tuple, list))
                or any(type(row) is not CertifiedLiquidityResolution for row in liquidity_certificates)
                or {row.source.ticker for row in liquidity_certificates} != tickers
                or any(row.source.session_date != controller.definition.session_date for row in liquidity_certificates)):
            raise ValueError("Program cohort1s market coverage differs from assignments")
        rows_by_ticker = await read_liquidity_cohort_async(reader_factory, liquidity_certificates,
            first_bucket=first_bucket, stop_bucket=stop_bucket, concurrency=concurrency)
    if (controller._runtime is not runtime or controller._program_strategy is not actor
            or controller._program_signal_runtime is not source or controller._journal_publisher is not journal_publisher
            or controller.definition is not definition or runtime.broker is not broker or runtime.config is not config
            or runtime.order_manager is not manager or runtime.portfolio is not portfolio
            or runtime.journal is not journal or controller._journal is not journal
            or runtime.last_event_time != before_clock or journal.latest_sequence(controller.run_id) != before_sequence
            or source.typed_source_cursor() != before_source
            or source_publisher._closing or source_publisher._fatal is not None
            or journal_publisher._error is not None or journal_publisher._terminal_task is not None
            or journal_publisher._task is not None and not journal_publisher._task.done()
            or (journal_publisher.fenced_sequence, journal_publisher._batch_id, journal_publisher._source_cursor) != before_fence):
        raise ValueError("Program cohort ownership changed during liquidity I/O")
    if set(rows_by_ticker) != tickers:
        raise ValueError("Program cohort read lacks exact certified ticker coverage")
    boundaries = {}
    for ticker, rows in rows_by_ticker.items():
        for row in rows:
            boundary = (row["bucket_index"] + 1) * resolution - SESSION_OPEN_OFFSET_MS
            if row["ticker"] != ticker or row["resolution_ms"] != resolution or not after < boundary <= through:
                raise ValueError("Program cohort read returned a foreign liquidity clock")
            group = boundaries.setdefault(boundary, {})
            if ticker in group:
                raise ValueError("Program cohort read repeated a liquidity ticker/clock")
            group[ticker] = row
    packet = initial_packet
    replay_blocks = tuple({id(block): block for block in blocks.values()}.values())
    decision_count = source_count = liquidity_count = 0
    started = False
    try:
        for boundary in range(after + resolution, through + 1, resolution):
            if controller._stop_requested:
                return ProgramCohortResult("stopped", liquidity_count, source_count, decision_count, packet)
            await controller._wait_until_active()
            if controller._stop_requested:
                return ProgramCohortResult("stopped", liquidity_count, source_count, decision_count, packet)
            if (controller._runtime is not runtime or controller._program_strategy is not actor
                    or controller._program_signal_runtime is not source
                    or controller._journal_publisher is not journal_publisher
                    or runtime.broker is not broker or runtime.config is not config
                    or runtime.order_manager is not manager or runtime.portfolio is not portfolio
                    or runtime.journal is not journal or controller._journal is not journal):
                raise ValueError("Program cohort lost runtime ownership")
            at = market_day_boundary(day, boundary)
            started = True
            rows = tuple(row for _, row in sorted(boundaries.get(boundary, {}).items()))
            if rows:
                await runtime.process_liquidity_boundary(rows, at=at)
            else:
                await runtime.advance_program_session_clock(at)
            liquidity_count += 1
            controller.current_time = at
            if boundary % 1000:
                continue
            cursor = source.typed_source_cursor()
            if cursor[2] is None or cursor[2] < at:
                await publish_prepared_program_source_clock(controller, projection, source_publisher,
                    at=at, catalogs=catalogs)
            else:
                source.typed_source_boundary(at)
            source_count += 1
            results = await controller.dispatch_prepared_program_clock(blocks, population, at=at,
                assignment_order=order, execution_policy=execution_policy,
                protection_profiles=protection_profiles_at(at), outside_rth=outside_rth,
                terminal=terminal and boundary == through, qualify=True,
                policy_blocks=replay_blocks,
                maximum_events=maximum_events, initial_packet=packet)
            for _, result in results:
                packet = result.get("policy_packet", packet)
                decision_count += int("qualification" in result)
        return ProgramCohortResult("completed", liquidity_count, source_count, decision_count, packet)
    except BaseException as exc:
        if started:
            controller._program_dispatch_failure = exc
        raise
