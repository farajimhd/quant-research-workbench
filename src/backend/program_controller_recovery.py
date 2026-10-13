"""Cold controller adoption through the shared financial and policy gates.

This composition grants no app admission or operational restart permission.
The caller owns its assembled writer and inert runtime until adoption succeeds.
"""
import asyncio
from dataclasses import dataclass, field

from .qmd_columnar_history_transport import _owned_worker
from .program_financial_recovery import qualify_program_financial_runtime
from .program_policy_recovery import verify_recovered_program_positions, program_policy_fingerprint

_ADOPTION_PROOF = object()


@dataclass(frozen=True, slots=True)
class RecoveredProgramController:
    controller: object = field(repr=False)
    owners: tuple = field(repr=False)
    packet: object = field(repr=False)
    anchor: object
    configuration_hash: str
    _proof: object = field(repr=False)


async def adopt_recovered_program_controller(controller, *, assembly, evidence,
        runtime, packet, source, population, oms_image, anchor, lease,
        keeper_session, broker_contract):
    """Reserve the existing startup slot until cold custody settles."""
    from .replay_run_service import ReplayRunController
    if type(controller) is not ReplayRunController or getattr(controller, "_program_startup_active", False):
        raise RuntimeError("Programme startup already owns this controller")
    controller._program_startup_active = True
    try:
        return await _adopt_recovered_program_controller(controller, assembly=assembly,
            evidence=evidence, runtime=runtime, packet=packet, source=source,
            population=population, oms_image=oms_image, anchor=anchor, lease=lease,
            keeper_session=keeper_session, broker_contract=broker_contract)
    finally:
        controller._program_startup_active = False


async def _adopt_recovered_program_controller(controller, *, assembly, evidence,
        runtime, packet, source, population, oms_image, anchor, lease,
        keeper_session, broker_contract):
    """Qualify existing actors, then atomically transfer their controller custody.

    All bootstrap inputs must already have independent normalized read proofs.
    This never initializes a fresh broker, opens a writer, or starts a run task.
    On rejection the caller retains ownership and must drain/close its objects.
    """
    from .replay_run_service import ReplayRunController
    from .backtest_fixed_journal_bootstrap import FixedJournalAssembly, FixedV4JournalPreflightToken
    from .backtest_fixed_running_anchor import FixedRunningPrefixAnchor
    from .program_recovery_evidence import ProgramRecoveryEvidence
    from .backtest_program_journal_bootstrap import _configuration_hash
    from .backtest_typed_publisher import BacktestTypedJournalPublisher
    from .backtest_journal_memory import BacktestMemoryJournal
    from .backtest_v4_keeper_lease import BacktestV4KeeperLease
    from .signal_stream_runtime_service import SignalStreamRuntime
    from .qmd_columnar_population import CertifiedPopulationWindow
    from .backtest_v4_run_context import historical_strategy_one_portfolio_profiles
    from .program_policy_recovery import RecoveredProgramPolicy, _RECOVERY_PROOF
    from .program_saved_definition import _freeze
    from src.trading_runtime.runtime import TradingRuntime, typed_run_config_payload
    from src.trading_runtime.keeper_session import ManagedKeeperSession
    from src.trading_runtime.backtest_broker_contract import BacktestBrokerContract
    from src.trading_runtime.arte_broker_match_snapshot import verify_shared_broker_snapshot, _codec_rows
    from src.trading_runtime.oms_assignment_observation import observe_assignment_orders
    if (type(controller) is not ReplayRunController or type(assembly) is not FixedJournalAssembly
            or type(evidence) is not ProgramRecoveryEvidence or type(runtime) is not TradingRuntime
            or type(anchor) is not FixedRunningPrefixAnchor or type(lease) is not BacktestV4KeeperLease
            or type(keeper_session) is not ManagedKeeperSession or type(broker_contract) is not BacktestBrokerContract
            or type(source) is not SignalStreamRuntime or type(population) is not CertifiedPopulationWindow):
        raise ValueError("Programme cold adoption requires its native authorities")
    if type(packet) is not RecoveredProgramPolicy or packet._proof is not _RECOVERY_PROOF:
        raise ValueError("Programme cold adoption requires its recovered policy proof")
    definition, template = controller.definition, controller._program_strategy
    if template is None:
        raise ValueError("Programme cold adoption lacks its installed template")
    prefix, financial = evidence.definition.prefix, evidence.financial
    journal, publisher = assembly.journal, assembly.publisher
    at = financial.completed_at
    profiles, groups = historical_strategy_one_portfolio_profiles(definition.configuration_revision["payload"])
    configuration_hash = _configuration_hash(definition)
    from .backtest_v4_saved_review import _utc_timestamp
    created_at = _utc_timestamp(evidence.definition.context["started_at"])
    def require_scope():
        if (controller.definition is not definition or controller._program_strategy is not template
                or controller._runtime is not None or controller._journal is not None
                or controller._journal_publisher is not None or controller._journal_writer is not None
                or controller._fixed_v4_lease is not None or controller._fixed_keeper_session is not None
                or controller._task is not None or controller.status != "created" or controller._stop_requested
                or controller._program_dispatch_failure is not None
                or evidence.run_definition != definition or financial.prefix != prefix or prefix.status != "running"
                or type(assembly.token) is not FixedV4JournalPreflightToken
                or assembly.token.run_id != controller.run_id or assembly.token.configuration_hash != configuration_hash
                or assembly.token.account_ids != runtime.config.account_ids
                or assembly.token.market_plan_token != definition.market_data_plan.get("token")
                or evidence.definition.context["configuration_hash"] != configuration_hash
                or evidence.definition.context["code_hash"] != template.program_release.header["code_hash"]
                or _freeze(definition.configuration_revision) != _freeze(evidence.definition.revision)
                or prefix.run_id != controller.run_id or runtime.run_id != controller.run_id
                or runtime.journal is not journal or runtime.last_event_time != at
                or runtime._typed_oms_recovered is not True
                or runtime.broker.config.liquidity_resolution_ms != controller._program_liquidity_resolution_ms
                or getattr(runtime.portfolio, "_typed_backtest_recovery", False) is not True
                or runtime.strategy.program_release.header != template.program_release.header
                or runtime.strategy.assignments() != template.assignments() or runtime.strategy._listings != template._listings
                or runtime.config.account_ids != tuple(evidence.definition.context["account_ids"])
                or {key: state.profile for key, state in runtime.portfolio.states.items()}
                   != {profile.account_id: profile for profile in profiles}
                or runtime.portfolio.groups != {group.group_id: group for group in groups}
                or type(publisher) is not BacktestTypedJournalPublisher or publisher.journal is not journal
                or type(journal) is not BacktestMemoryJournal
                or publisher.writer is not assembly.writer or publisher.expected_config != typed_run_config_payload(runtime.config)
                or assembly.writer.journal_profile != "backtest_v4" or assembly.writer.run_id != controller.run_id
                or publisher.exact_oms_prices is not True or publisher._error is not None
                or publisher._terminal_task is not None or publisher._task is not None and not publisher._task.done()
                or publisher.fenced_sequence != prefix.last_sequence or publisher._batch_id != prefix.last_batch_id
                or publisher._source_cursor != prefix.source_cursor or journal.pending_record_count
                or journal.latest_sequence(controller.run_id) != prefix.last_sequence
                or lease.run_id != controller.run_id or lease.epoch < 2 or lease.owner._session is not keeper_session
                or not keeper_session.writable or getattr(assembly.writer._client, "backtest_v4_lease", None) is not lease
                or source._typed_scope is None or population.source_revision != source._typed_scope[1]
                or source._typed_scope[0] != definition.configuration_revision["revision_id"]
                or (anchor.run_id, anchor.journal_sequence, anchor.batch_id, anchor.source_cursor, anchor.completed_at)
                   != (controller.run_id, prefix.last_sequence, prefix.last_batch_id, prefix.source_cursor, at)
                or packet.journal_sequence != prefix.last_sequence
                or program_policy_fingerprint(packet.actor) != packet.state_hash
                or _configuration_hash(definition) != configuration_hash):
            raise ValueError("Programme cold adoption lost its exact idle scope")
    require_scope()
    broker_contract.require_config(runtime.broker.config, run_id=controller.run_id,
        content_hash=broker_contract.row["content_hash"], liquidity_plan_hash=broker_contract.row["liquidity_plan_hash"])
    broker = verify_shared_broker_snapshot(financial.broker, broker_contract=broker_contract,
        run_id=controller.run_id, checkpoint_sequence=prefix.last_sequence,
        expected_content_hash=financial.broker.snapshot["content_hash"])
    boundary = source.typed_source_boundary(at)
    hashes = source.typed_source_commit_hashes()[:boundary[0]]
    await _owned_worker(lease.assert_current)
    synchronized = await _owned_worker(qualify_program_financial_runtime, runtime, financial.portfolio,
        prefix, _codec_rows(broker), oms_image=oms_image)
    accounts = runtime.config.account_ids
    results = await asyncio.gather(*(runtime.broker.positions(account) for account in accounts),
        *(runtime.broker.account_ledger(account) for account in accounts),
        runtime.broker.trades(), runtime.broker.canonical_orders(), return_exceptions=True)
    for result in results:
        if isinstance(result, BaseException): raise result
    observations = {assignment.assignment_id: observe_assignment_orders(runtime.order_manager,
        account_id=assignment.account_id, assignment_id=assignment.assignment_id,
        ticker=assignment.ticker, at=at) for assignment in template.assignments()}
    await _owned_worker(verify_recovered_program_positions, packet, template,
        run_id=controller.run_id, source_hashes=hashes, at=at,
        broker_positions=dict(zip(accounts, results[:len(accounts)], strict=True)),
        broker_ledgers=dict(zip(accounts, results[len(accounts):-2], strict=True)),
        broker_executions=results[-2], broker_orders=results[-1], order_manager=runtime.order_manager,
        portfolio=runtime.portfolio, portfolio_recovery=synchronized, observations=observations, population=population)
    await _owned_worker(lease.assert_current)
    require_scope()
    if source.typed_source_boundary(at) != boundary or source.typed_source_commit_hashes()[:boundary[0]] != hashes:
        raise RuntimeError("Programme cold adoption source advanced during qualification")
    # No await below: transfer the same objects whose financial and policy
    # projections passed; no alternative position or OMS state is introduced.
    runtime.strategy = packet.actor
    controller._strategy = controller._program_strategy = packet.actor
    controller._runtime, controller._journal, controller._planner = runtime, journal, runtime.intent_planner
    controller._journal_writer, controller._journal_publisher = assembly.writer, publisher
    controller._fixed_v4_lease, controller._fixed_keeper_session = lease, keeper_session
    controller._program_signal_runtime, controller._program_broker_contract = source, broker_contract
    controller.current_time = at
    controller._source_cursor = dict(session_date=anchor.session_date, boundary_ms=anchor.boundary_ms,
        sequence=anchor.market_sequence)
    controller._frame_cursor = {}
    controller.created_at = created_at
    owners = (definition, runtime, packet.actor, journal, publisher, assembly.writer, source,
        runtime.broker, runtime.portfolio, runtime.order_manager, lease, keeper_session, broker_contract)
    proof = RecoveredProgramController(controller, owners, packet, anchor, configuration_hash, _ADOPTION_PROOF)
    controller._program_recovered_controller = proof
    return proof
