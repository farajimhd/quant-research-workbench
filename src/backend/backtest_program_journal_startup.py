"""Controller-owned asynchronous adoption of the shared programme bootstrap."""
import asyncio
from datetime import date

from .backtest_program_journal_bootstrap import publish_and_assemble_program_v4_journal, _configuration_hash
from .backtest_fixed_journal_bootstrap import FixedJournalAssembly
from .program_policy_recovery import program_policy_fingerprint
from src.trading_runtime.arte_backtest_definition import prepare_backtest_definition
from src.trading_runtime.arte_journal_writer import ArteJournalWriter
from src.trading_runtime.keeper_session import ManagedKeeperSession
from src.trading_runtime.keeper_ownership import KeeperOwnershipCoordinator
from src.trading_runtime.backtest_broker_contract import BacktestBrokerContract
from src.trading_runtime.backtest_journal_price_contract import BacktestJournalPriceContract
from src.trading_runtime.runtime import RunConfig


async def _settle_owned_task(task):
    """Drain the same task despite repeated caller cancellation."""
    while True:
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.done():
                return task.result()


async def _close_unadopted_assembly(assembly):
    try:
        await _settle_owned_task(asyncio.create_task(asyncio.to_thread(assembly.writer.close)))
    finally:
        assembly.journal.close()


async def attach_program_journal(controller, *, context_client, read_client, writer_client,
        terminal_client, keeper_session, keeper, runtime_config, expected_release_hash,
        code_hash, fixed_market_parent_plan, fixed_market_execution_plan, journal_price_contract,
        expected_journal_price_contract_hash, broker_contract, expected_broker_contract_hash,
        liquidity_plan_hash, definition_lease, attempt_id, projection_certifier,
        writer_factory=ArteJournalWriter, batch_size=1024, queue_capacity=8):
    """Adopt only unchanged fresh ownership; never abandon a bootstrap worker.

    On success the controller owns the writer, journal, run lease and Keeper
    session. Context/read/terminal clients remain caller-owned. On failure the
    caller retains its leases/session; any returned worker is drained and closed.
    This API does not grant operational permission or open normal launch admission.
    """
    from .replay_run_service import ReplayRunController
    if type(controller) is not ReplayRunController or getattr(controller, "_program_startup_active", False):
        raise RuntimeError("Programme startup already owns this controller or has no native owner")
    actor = controller._program_strategy
    if (actor is None or controller._runtime is not None or controller._journal is not None
            or controller._journal_writer is not None or controller._journal_publisher is not None
            or controller._fixed_keeper_session is not None or controller._fixed_v4_lease is not None
            or controller._resume_state is not None or controller._program_dispatch_failure is not None
            or controller._stop_requested
            or type(keeper_session) is not ManagedKeeperSession
            or not keeper_session.writable
            or type(keeper) is not KeeperOwnershipCoordinator
            or keeper._client is not keeper_session.client
            or getattr(writer_client, "backtest_v4_lease", None) is None
            or writer_client.backtest_v4_lease.owner._session is not keeper_session
            or type(runtime_config) is not RunConfig
            or type(broker_contract) is not BacktestBrokerContract
            or type(journal_price_contract) is not BacktestJournalPriceContract
            or broker_contract.config.liquidity_resolution_ms != controller._program_liquidity_resolution_ms
            or runtime_config.run_id != controller.run_id
            or runtime_config.account_ids != tuple(dict.fromkeys(row.account_id for row in actor.assignments()))):
        raise RuntimeError("Programme startup lacks its fresh exclusive runtime and session scope")
    definition, run_id = controller.definition, controller.run_id
    month = date.fromisoformat(journal_price_contract.row["run_month"])
    prepared = prepare_backtest_definition(run_id, definition, run_month=month)
    configuration_hash, policy_hash = _configuration_hash(definition), program_policy_fingerprint(actor)
    lease = writer_client.backtest_v4_lease
    controller._program_startup_active = True
    assembly = None
    job = asyncio.create_task(asyncio.to_thread(publish_and_assemble_program_v4_journal,
        context_client, read_client, writer_client, terminal_client,
        runtime_config=runtime_config, definition=definition, release=actor.program_release,
        expected_release_hash=expected_release_hash, code_hash=code_hash, started_at=controller.created_at,
        fixed_market_parent_plan=fixed_market_parent_plan, fixed_market_execution_plan=fixed_market_execution_plan,
        journal_price_contract=journal_price_contract,
        expected_journal_price_contract_hash=expected_journal_price_contract_hash,
        broker_contract=broker_contract, expected_broker_contract_hash=expected_broker_contract_hash,
        liquidity_plan_hash=liquidity_plan_hash, keeper=keeper, definition_lease=definition_lease,
        attempt_id=attempt_id, projection_certifier=projection_certifier,
        writer_factory=writer_factory, batch_size=batch_size, queue_capacity=queue_capacity))
    try:
        assembly = await asyncio.shield(job)
        if (controller.definition is not definition or controller.run_id != run_id
                or controller._program_strategy is not actor or controller._runtime is not None
                or controller._journal is not None or controller._journal_writer is not None
                or controller._journal_publisher is not None or controller._fixed_v4_lease is not None
                or controller._fixed_keeper_session is not None or controller._stop_requested
                or controller._program_dispatch_failure is not None
                or _configuration_hash(definition) != configuration_hash
                or prepare_backtest_definition(run_id, definition, run_month=month) != prepared
                or program_policy_fingerprint(actor) != policy_hash
                or writer_client.backtest_v4_lease is not lease
                or type(assembly) is not FixedJournalAssembly
                or assembly.publisher.exact_oms_prices is not True):
            raise RuntimeError("Programme startup ownership changed before journal adoption")
        controller._fixed_v4_account_ids = runtime_config.account_ids
        controller._attach_fixed_journal_assembly(assembly)
        controller._fixed_keeper_session = keeper_session
        controller._fixed_v4_lease = lease
        controller._program_broker_contract = broker_contract
    except BaseException as exc:
        controller._program_dispatch_failure = exc
        if assembly is None and not job.cancelled():
            try:
                assembly = await _settle_owned_task(job)
            except BaseException as worker_exc:
                if worker_exc is not exc:
                    exc.__cause__ = worker_exc
        if type(assembly) is FixedJournalAssembly:
            await _close_unadopted_assembly(assembly)
        raise
    finally:
        controller._program_startup_active = False
