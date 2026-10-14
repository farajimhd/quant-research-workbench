"""Prepared programme handoff into the existing app controller lifecycle."""
from dataclasses import dataclass

_PLAN_SECRET = object()


def _owners(controller):
    runtime = controller._runtime
    publisher = controller._journal_publisher
    return (controller.definition, runtime, controller._program_strategy, controller._program_signal_runtime,
        controller._journal, publisher, publisher.writer, runtime.broker, runtime.config,
        runtime.portfolio, runtime.order_manager, controller._fixed_v4_lease,
        controller._fixed_keeper_session, controller._program_broker_contract)


@dataclass(frozen=True)
class _PreparedProgramLaunch:
    cohorts: tuple
    owners: tuple
    configuration_hash: str
    proof: object
    clock: object
    source_cursor: tuple
    journal_sequence: int
    policy_hash: str
    checkpoint_connection_factory: object
    checkpoint_concurrency: int
    recovered: object = None

    def require_execution_owner(self, controller):
        from .backtest_program_journal_bootstrap import _configuration_hash
        if (self.proof is not _PLAN_SECRET
                or any(before is not after for before, after in zip(self.owners, _owners(controller)))
                or _configuration_hash(controller.definition) != self.configuration_hash
                or controller._runtime_finished or controller._program_dispatch_failure is not None
                or not controller._fixed_keeper_session.writable
                or controller._journal_publisher._error is not None
                or controller._journal_publisher._terminal_task is not None):
            raise RuntimeError("Prepared programme launch lost its admitted ownership")

    def require_current(self, controller):
        self.require_execution_owner(controller)
        if self.recovered is not None:
            from .program_controller_recovery import require_recovered_program_controller
            if require_recovered_program_controller(controller) is not self.recovered:
                raise RuntimeError("Programme cold launch changed its adopted proof")
        from .program_policy_recovery import program_policy_fingerprint
        if (controller._runtime.last_event_time != self.clock
                or controller._program_signal_runtime.typed_source_cursor() != self.source_cursor
                or controller._journal.latest_sequence(controller.run_id) != self.journal_sequence
                or program_policy_fingerprint(controller._program_strategy) != self.policy_hash):
            raise RuntimeError("Prepared programme launch lost its admitted ownership")


async def bind_prepared_program_launch(controller, cohorts, *, checkpoint_connection_factory, checkpoint_concurrency):
    """Bind complete producer-prepared coverage only after typed adoption.

    No schema publication, producer, feature build, broker or run starts here.
    The runtime and source must already belong to the admitted preparation path.
    """
    from .replay_run_service import ReplayRunController
    from .backtest_program_session import validate_prepared_program_session
    from .backtest_v4_keeper_lease import BacktestV4KeeperLease
    from .qmd_columnar_history_transport import _owned_worker
    from .backtest_program_journal_bootstrap import _configuration_hash
    from src.trading_runtime.keeper_session import ManagedKeeperSession
    from src.trading_runtime.backtest_broker_contract import BacktestBrokerContract
    from .program_policy_recovery import program_policy_fingerprint
    from .backtest_typed_publisher import BacktestTypedJournalPublisher
    from .backtest_journal_memory import BacktestMemoryJournal
    if (not callable(checkpoint_connection_factory) or type(checkpoint_concurrency) is not int
            or not 1 <= checkpoint_concurrency <= 8
            or type(controller) is not ReplayRunController or controller._program_strategy is None
            or controller._task is not None or controller.status != "created"
            or getattr(controller, "_program_launch_plan", None) is not None
            or controller._runtime is None or controller._program_signal_runtime is None
            or type(controller._journal_publisher) is not BacktestTypedJournalPublisher
            or type(controller._journal) is not BacktestMemoryJournal or controller._journal_writer is None
            or controller._journal_publisher.journal is not controller._journal
            or controller._journal_writer.journal_profile != "backtest_v4"
            or controller._journal_publisher._task is not None and not controller._journal_publisher._task.done()
            or getattr(controller, "_program_session_active", False)
            or getattr(controller, "_program_cohort_active", False)
            or controller._journal_publisher.writer is not controller._journal_writer
            or controller._journal_publisher.exact_oms_prices is not True
            or controller._runtime.strategy is not controller._program_strategy
            or controller._runtime.journal is not controller._journal
            or controller._runtime.last_event_time not in (None, controller.definition.session_start)
            or controller._runtime.processed_events != 0 or controller.current_time is not None
            or type(controller._fixed_v4_lease) is not BacktestV4KeeperLease
            or controller._fixed_v4_lease.run_id != controller.run_id
            or type(controller._fixed_keeper_session) is not ManagedKeeperSession
            or type(getattr(controller, "_program_broker_contract", None)) is not BacktestBrokerContract
            or controller._stop_requested or controller._runtime_finished
            or controller._program_dispatch_failure is not None):
        raise RuntimeError("Programme launch requires its fresh adopted typed preparation")
    work, _, _ = validate_prepared_program_session(controller, cohorts)
    controller._program_broker_contract.require_config(controller._runtime.broker.config,
        run_id=controller.run_id, content_hash=controller._program_broker_contract.row["content_hash"],
        liquidity_plan_hash=controller._program_broker_contract.row["liquidity_plan_hash"])
    plan = _PreparedProgramLaunch(work, _owners(controller), _configuration_hash(controller.definition), _PLAN_SECRET,
        controller._runtime.last_event_time, controller._program_signal_runtime.typed_source_cursor(),
        controller._journal.latest_sequence(controller.run_id), program_policy_fingerprint(controller._program_strategy),
        checkpoint_connection_factory, checkpoint_concurrency)
    plan.require_current(controller)
    await _owned_worker(controller._fixed_v4_lease.assert_current)
    plan.require_current(controller)
    controller._program_launch_plan = plan


async def bind_recovered_program_launch(controller, cohorts, *, checkpoint_connection_factory, checkpoint_concurrency):
    """Bind the same native engine from a qualified adopted cold cursor."""
    from .program_controller_recovery import require_recovered_program_controller
    from .backtest_program_session import (validate_prepared_program_session,
        _continue_fenced_session_work, require_program_session_owner)
    from .backtest_program_journal_bootstrap import _configuration_hash
    from .program_policy_recovery import program_policy_fingerprint
    from .qmd_columnar_history_transport import _owned_worker
    require_program_session_owner(controller)
    if (not callable(checkpoint_connection_factory) or type(checkpoint_concurrency) is not int
            or not 1 <= checkpoint_concurrency <= 8 or controller._task is not None
            or controller.status != "created" or getattr(controller, "_program_launch_plan", None) is not None
            or getattr(controller, "_program_startup_active", False)
            or getattr(controller, "_program_session_active", False)
            or getattr(controller, "_program_cohort_active", False)):
        raise RuntimeError("Programme cold launch lacks its idle prepared owner")
    recovered = require_recovered_program_controller(controller)
    work, _, _ = validate_prepared_program_session(controller, cohorts)
    # Verify that this full-origin plan has strictly remaining coverage. The
    # native session scheduler applies this same cursor when execution begins.
    await _continue_fenced_session_work(controller, work, recovered.packet)
    if require_recovered_program_controller(controller) is not recovered:
        raise RuntimeError("Programme cold launch changed during cursor verification")
    plan = _PreparedProgramLaunch(work, _owners(controller), _configuration_hash(controller.definition), _PLAN_SECRET,
        controller._runtime.last_event_time, controller._program_signal_runtime.typed_source_cursor(),
        controller._journal.latest_sequence(controller.run_id), program_policy_fingerprint(controller._program_strategy),
        checkpoint_connection_factory, checkpoint_concurrency, recovered)
    plan.require_current(controller)
    await _owned_worker(controller._fixed_v4_lease.assert_current)
    plan.require_current(controller)
    controller._program_launch_plan = plan


async def require_bound_program_start(controller):
    from .qmd_columnar_history_transport import _owned_worker
    plan = getattr(controller, "_program_launch_plan", None)
    if type(plan) is not _PreparedProgramLaunch:
        raise RuntimeError("Programme start lacks its prepared typed launch handoff")
    plan.require_current(controller)
    await _owned_worker(controller._fixed_v4_lease.assert_current)
    plan.require_current(controller)
    return plan


async def run_bound_program_engine(controller):
    plan = await require_bound_program_start(controller)
    controller.status = "running"
    controller._preparation_stage = "ready"
    controller._runtime_inputs_ready = True
    await controller._publish(force=True)
    try:
        continuation = {"initial_packet": plan.recovered.packet} if plan.recovered is not None else {}
        result = await controller.run_prepared_program_session(plan.cohorts, terminal=True, **continuation)
        await controller._finish("completed" if result.status == "evaluated" else "stopped")
    finally:
        # Session features belong to prepared execution, not retained UI state.
        controller._program_launch_plan = None
        controller._program_feature_cache = None
        controller._program_population_cache = None


async def checkpoint_bound_program_cursor(controller):
    """Publish only for a bound normal run; staged cohort APIs remain separate."""
    plan = getattr(controller, "_program_launch_plan", None)
    if plan is None:
        return None
    if type(plan) is not _PreparedProgramLaunch:
        raise RuntimeError("Programme checkpoint lacks its typed launch plan")
    plan.require_execution_owner(controller)
    return await controller.publish_program_broker_checkpoint(
        connection_factory=plan.checkpoint_connection_factory, concurrency=plan.checkpoint_concurrency)
