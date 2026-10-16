"""Owned central broker checkpoint submission at an acknowledged app cursor."""
import asyncio


def program_checkpoint_publication_factory(writer, *, session, lease):
    """Resolve child writers from the adopted journal, never a read factory."""
    from src.trading_runtime.arte_typed_insert_dispatch import TypedInsertDispatch
    from src.trading_runtime.keeper_session import ManagedKeeperSession
    from .backtest_v4_keeper_lease import BacktestV4KeeperLease
    client = writer._client
    dispatch = getattr(client, 'typed_insert_dispatch', None)
    factory = getattr(client, 'v4_insert_lane_factory', None)
    if (type(session) is not ManagedKeeperSession or type(lease) is not BacktestV4KeeperLease
            or writer.journal_profile != 'backtest_v4' or not callable(factory)
            or getattr(client, 'typed_insert_strict', False) is not True
            or type(dispatch) is not TypedInsertDispatch
            or getattr(client, 'manager_keeper_session', None) is not session
            or getattr(client, 'backtest_v4_lease', None) is not lease
            or dispatch.keeper is not session.client):
        raise RuntimeError('Programme checkpoint requires its adopted strict writer lanes')
    lease.assert_current()
    return factory


async def publish_program_broker_checkpoint(controller, *, connection_factory, concurrency):
    """Publish the native broker image without advancing market or strategy state.

    This is one checkpoint component. Full cold admission additionally needs
    financial, OMS, programme and certified input/source reconstruction.
    """
    from .replay_run_service import ReplayRunController
    from .backtest_program_session import require_program_session_owner
    from .backtest_journal_memory import BacktestMemoryJournal
    from .backtest_typed_publisher import BacktestTypedJournalPublisher
    from .program_policy_recovery import program_policy_fingerprint
    from .qmd_columnar_history_transport import _owned_worker
    from .backtest_program_journal_startup import _settle_owned_task
    from .backtest_market_data import market_day_boundary
    from src.trading_runtime.arte_broker_match_snapshot import project_shared_broker_snapshot
    from src.trading_runtime.arte_oms_observation_snapshot import project_shared_oms_observations
    from src.trading_runtime.backtest_broker_contract import BacktestBrokerContract
    from .signal_stream_runtime_service import SignalStreamRuntime
    if type(controller) is not ReplayRunController:
        raise ValueError("Programme checkpoint requires its native controller")
    require_program_session_owner(controller)
    runtime, actor = controller._runtime, controller._program_strategy
    journal, publisher = controller._journal, controller._journal_publisher
    contract = getattr(controller, "_program_broker_contract", None)
    if (not callable(connection_factory) or type(concurrency) is not int or not 1 <= concurrency <= 8
            or runtime is None or actor is None or runtime.strategy is not actor
            or type(journal) is not BacktestMemoryJournal or runtime.journal is not journal
            or type(publisher) is not BacktestTypedJournalPublisher or publisher.journal is not journal
            or type(controller._program_signal_runtime) is not SignalStreamRuntime
            or publisher.writer.journal_profile != "backtest_v4"
            or publisher.writer is not controller._journal_writer or not publisher.exact_oms_prices
            or type(contract) is not BacktestBrokerContract
            or controller._program_dispatch_failure is not None or controller._runtime_finished
            or getattr(controller, "_program_checkpoint_active", False)
            or getattr(controller, "_checkpoint_io_task", None) is not None
            or publisher._error is not None or publisher._terminal_task is not None
            or publisher._task is not None and not publisher._task.done()
            or journal.unfenced_records() or publisher.fenced_sequence != journal.latest_sequence(controller.run_id)):
        raise RuntimeError("Programme checkpoint requires its idle acknowledged typed ownership")
    at, cursor = controller.current_time, dict(controller._source_cursor)
    day = controller.definition.session_date
    boundary = cursor.get("boundary_ms")
    if (at is None or runtime.last_event_time != at or type(boundary) is not int
            or cursor.get("session_date") != day.isoformat()
            or at != market_day_boundary(day, boundary)
            or publisher._source_cursor != f"{day.isoformat()}:{boundary}"):
        raise ValueError("Programme checkpoint lacks its completed V4 market cursor")
    writer, broker = publisher.writer, runtime.broker
    source = controller._program_signal_runtime
    owners = (controller.definition, runtime, actor, journal, publisher, writer, broker,
        runtime.config, runtime.order_manager, runtime.portfolio, source, contract,
        controller._fixed_v4_lease, controller._fixed_keeper_session)
    sequence, batch, source_cursor = publisher.fenced_sequence, publisher._batch_id, source.typed_source_cursor()
    policy_hash = program_policy_fingerprint(actor)
    captured = broker.checkpoint_state()
    observed = runtime.order_manager.capture_observed_broker_states()
    accounts = tuple(runtime.config.account_ids)
    if not accounts or len(set(accounts)) != len(accounts) or set(accounts) != set(controller.account_ids):
        raise ValueError("Programme checkpoint account population differs from its run")
    portfolio_captures = tuple(runtime.portfolio.capture_recovery_snapshot(account_id,
        state_revision=sequence, snapshot_at=at) for account_id in sorted(accounts))
    def require_owner():
        current = (controller.definition, controller._runtime, controller._program_strategy,
            controller._journal, controller._journal_publisher, publisher.writer, runtime.broker,
            runtime.config, runtime.order_manager, runtime.portfolio, controller._program_signal_runtime,
            controller._program_broker_contract, controller._fixed_v4_lease, controller._fixed_keeper_session)
        if (any(before is not after for before, after in zip(owners, current))
                or runtime.last_event_time != at or controller.current_time != at
                or controller._source_cursor != cursor or source.typed_source_cursor() != source_cursor
                or publisher.fenced_sequence != sequence or publisher._batch_id != batch
                or journal.latest_sequence(controller.run_id) != sequence or journal.unfenced_records()
                or program_policy_fingerprint(actor) != policy_hash):
            raise RuntimeError("Programme checkpoint ownership changed during publication")
    controller._program_checkpoint_active = True
    controller._program_checkpoint_task = asyncio.current_task()
    controller._checkpoint_io_task = asyncio.current_task()
    submitted = False
    try:
        rows = await _owned_worker(project_shared_broker_snapshot, run_id=controller.run_id,
            session_date=day, checkpoint_sequence=sequence, boundary_ms=boundary,
            state=captured, simulation_config=broker.config, broker_contract=contract)
        require_owner()
        # Parallel publication borrows the existing strict writer authority.
        # The caller's checkpoint reader intentionally has readonly=1 and
        # must never acquire insert dispatch merely by being used here.
        publication_factory = await _owned_worker(program_checkpoint_publication_factory, writer,
            session=controller._fixed_keeper_session, lease=controller._fixed_v4_lease)
        require_owner()
        future = writer.submit_shared_broker_match_snapshot(rows, broker_contract=contract,
            journal_batch_id=batch, connection_factory=publication_factory, concurrency=concurrency)
        submitted = True
        job = asyncio.ensure_future(asyncio.wrap_future(future))
        try:
            receipt = await asyncio.shield(job)
        except asyncio.CancelledError:
            await _settle_owned_task(job)
            raise
        require_owner()
        if receipt != batch:
            raise RuntimeError("Programme broker checkpoint changed its V4 batch receipt")
        observations = await _owned_worker(project_shared_oms_observations,
            run_id=controller.run_id, session_date=day, checkpoint_sequence=sequence,
            boundary_ms=boundary, groups=observed)
        require_owner()
        future = writer.submit_shared_oms_observation_snapshot(observations, journal_batch_id=batch)
        job = asyncio.ensure_future(asyncio.wrap_future(future))
        try:
            receipt = await asyncio.shield(job)
        except asyncio.CancelledError:
            await _settle_owned_task(job)
            raise
        require_owner()
        if receipt != batch:
            raise RuntimeError("Programme OMS checkpoint changed its V4 batch receipt")
        for capture in portfolio_captures:
            require_owner()
            financial = writer.submit_running_portfolio_snapshot(journal_batch_id=batch, captured=capture)
            job = asyncio.ensure_future(asyncio.wrap_future(financial))
            try:
                receipt = await asyncio.shield(job)
            except asyncio.CancelledError:
                await _settle_owned_task(job)
                raise
            require_owner()
            if receipt != batch:
                raise RuntimeError("Programme account checkpoint changed its V4 batch receipt")
        return rows
    except BaseException as failure:
        if submitted:
            controller._program_dispatch_failure = failure
        raise
    finally:
        controller._program_checkpoint_active = False
        controller._program_checkpoint_task = None
        controller._checkpoint_io_task = None
