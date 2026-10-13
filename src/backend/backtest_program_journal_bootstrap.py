"""Programme launch handoff through the existing normalized V4 authorities.

The caller owns all connections and leases, and separately admits the release,
certified inputs and operational run. This module installs no schema, prepares
no market data and never substitutes a legacy frame or disk journal.
"""
from collections.abc import Mapping
from datetime import date
import re
from uuid import UUID
from hashlib import sha256
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor

from .backtest_v4_run_context import program_v4_context_rows
from .backtest_fixed_market_authority import _validate_plans
from .backtest_fixed_run_context import publish_fixed_run_context
from .backtest_fixed_journal_bootstrap import (
    assemble_fixed_v4_journal, _verified_v4_token, _v4_cold_reader_preflight,
    _V4PublishedContextSeal, _V4_CONTEXT_SECRET)
from .backtest_v4_keeper_lease import BacktestV4KeeperLease
from src.trading_runtime.arte_journal_writer import (
    ArteJournalWriter, _v4_preflight, load_typed_run_context)
from src.trading_runtime.arte_journal_schema import fixed_backtest_v2_preflight, storage_preflight
from src.trading_runtime.arte_journal_commit_v4 import MAX_V4_COMMIT_EVENTS
from src.trading_runtime.arte_typed_insert_dispatch import TypedInsertDispatch
from src.trading_runtime.arte_backtest_definition import prepare_backtest_definition, publish_backtest_definition
from src.trading_runtime.backtest_journal_price_contract import BacktestJournalPriceContract, PRICE_CONTRACT
from src.trading_runtime.keeper_ownership import KeeperOwnershipCoordinator, KeeperUnavailable
from src.trading_runtime.drawdown_measure_authority import bind_run_drawdown_authority
from src.trading_runtime.runtime import RunMode
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.backtest_broker_contract import BacktestBrokerContract, read_broker_contract
from src.trading_runtime.columnar_strategy_schema import BROKER


def _configuration_hash(definition):
    revision = definition.configuration_revision
    digest = sha256(canonical_json(revision["payload"]).encode("utf-8")).hexdigest()
    if digest != revision.get("content_hash"):
        raise ValueError("Programme configuration differs from its immutable payload hash")
    return digest


def _read_cold_pair(read_client, terminal_client, operation):
    """Run one operation per distinct connection; settle and inspect both."""
    if read_client is terminal_client or not callable(operation):
        raise ValueError("Programme cold reads require two distinct owned connections")
    values, failures = [], []
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="programme-cold-read") as pool:
        futures = [pool.submit(operation, client) for client in (read_client, terminal_client)]
        for future in futures:
            try:
                values.append(future.result())
            except BaseException as exc:
                values.append(None)
                failures.append(exc)
    if len(failures) == 1:
        raise failures[0]
    if failures:
        raise BaseExceptionGroup("Programme cold reads failed", failures)
    return tuple(values)


def publish_and_assemble_program_v4_journal(context_client, read_client, writer_client,
        terminal_client, *, runtime_config, definition, release, expected_release_hash,
        code_hash, started_at, fixed_market_parent_plan, fixed_market_execution_plan,
        journal_price_contract, expected_journal_price_contract_hash, keeper,
        broker_contract, expected_broker_contract_hash, liquidity_plan_hash,
        definition_lease, attempt_id, projection_certifier, writer_factory=ArteJournalWriter,
        batch_size=1024, queue_capacity=8):
    """Preflight before creation; publish context and definition before worker.

    Any failure after context publication needs cold recovery of that same run.
    Caller cleanup must drain an opened writer before releasing its run lease.
    Synchronous control I/O belongs on the controller's existing owned worker.
    """
    dispatch = getattr(context_client, "typed_insert_dispatch", None)
    runner_dispatch = getattr(writer_client, "typed_insert_dispatch", None)
    owner = getattr(writer_client, "backtest_v4_lease", None)
    if (len({id(context_client), id(read_client), id(writer_client), id(terminal_client)}) != 4
            or getattr(context_client, "typed_insert_strict", False) is not True
            or getattr(writer_client, "typed_insert_strict", False) is not True
            or not isinstance(dispatch, TypedInsertDispatch)
            or not isinstance(runner_dispatch, TypedInsertDispatch)
            or dispatch.keeper is not runner_dispatch.keeper
            or not isinstance(keeper, KeeperOwnershipCoordinator)
            or keeper._client is not dispatch.keeper
            or not isinstance(owner, BacktestV4KeeperLease)
            or any(getattr(client, "program_decision_profile", False) is not True
                for client in (read_client, writer_client, terminal_client))
            or not callable(projection_certifier) or not callable(writer_factory)
            or type(batch_size) is not int or not 1 <= batch_size <= MAX_V4_COMMIT_EVENTS
            or type(queue_capacity) is not int or not 1 <= queue_capacity <= 64):
        raise ValueError("Programme launch lacks bounded exclusive shared-Keeper authorities")
    run, config = program_v4_context_rows(runtime_config, release=release,
        expected_release_hash=expected_release_hash, execution_interval=definition.execution_interval,
        configuration_hash=_configuration_hash(definition), code_hash=code_hash,
        market_plan_token=fixed_market_parent_plan.token, started_at=started_at)
    run_id = run["run_id"]
    if (definition.mode is not RunMode.BACKTEST or definition.execution_mode != "strategy"
            or owner.run_id != run_id or definition.session_date != runtime_config.anchor_date
            or definition.market_data_plan.get("token") != fixed_market_parent_plan.token
            or (definition.configuration_revision["payload"]["strategy"].get("strategy_id"),
                definition.configuration_revision["payload"]["strategy"].get("revision")) !=
               (runtime_config.strategy_id, runtime_config.strategy_revision)):
        raise ValueError("Programme definition differs from its pinned native run")
    _validate_plans(fixed_market_parent_plan, fixed_market_execution_plan)
    UUID(attempt_id)
    month = date.fromisoformat(run["run_month"])
    prepared_definition = prepare_backtest_definition(run_id, definition, run_month=month)
    if type(journal_price_contract) is not BacktestJournalPriceContract:
        raise ValueError("Programme launch requires its normalized journal price contract")
    journal_price_contract.require_scope(run_id=run_id, run_month=month,
        configuration_hash=run["configuration_hash"], content_hash=expected_journal_price_contract_hash)
    from .replay_run_service import _simulation_config
    if type(broker_contract) is not BacktestBrokerContract:
        raise ValueError("Programme launch requires its normalized broker contract")
    selected_simulation = replace(_simulation_config(definition),
        liquidity_resolution_ms=broker_contract.config.liquidity_resolution_ms)
    broker_contract.require_config(selected_simulation, run_id=run_id,
        content_hash=expected_broker_contract_hash, liquidity_plan_hash=liquidity_plan_hash)
    resource = f"backtest-definition:{run_id}"
    if (not isinstance(definition_lease, Mapping) or definition_lease.get("resource_id") != resource
            or type(definition_lease.get("owner_id")) is not str or not definition_lease["owner_id"]
            or type(definition_lease.get("epoch")) is not int or definition_lease["epoch"] < 1):
        raise ValueError("Programme launch lacks its exact definition claim")

    def require_owners():
        owner.assert_current()
        if not keeper.portfolio_admission_lease_is_current(resource,
                owner_id=definition_lease["owner_id"], epoch=definition_lease["epoch"]):
            raise KeeperUnavailable("Programme definition launch claim is no longer current")
        if (_configuration_hash(definition) != run["configuration_hash"]
                or prepare_backtest_definition(run_id, definition, run_month=month) != prepared_definition):
            raise RuntimeError("Programme definition changed during launch handoff")

    require_owners()
    drawdown = bind_run_drawdown_authority(run_id=run_id, expected_config=config,
        configuration_hash=run["configuration_hash"], configuration_revision=definition.configuration_revision)
    fixed_backtest_v2_preflight(context_client)
    def preflight_cold(client):
        from src.trading_runtime.rule_decision_writer_preflight import program_checkpoint_storage_contracts
        _v4_cold_reader_preflight(client)
        storage_preflight(client, tables=(PRICE_CONTRACT, BROKER, *program_checkpoint_storage_contracts()))
    _read_cold_pair(read_client, terminal_client, preflight_cold)
    seal = _v4_preflight(writer_client)
    certificate = projection_certifier()
    if type(certificate) is not str or re.fullmatch(r"[0-9a-f]{64}", certificate) is None:
        raise RuntimeError("Programme launch projector cannot certify emitted families")
    require_owners()
    published = publish_fixed_run_context(context_client, read_client, terminal_client, dispatch,
        run=run, config=config, account_ids=runtime_config.account_ids)
    require_owners()
    if load_typed_run_context(writer_client, run_id) != published:
        raise RuntimeError("Programme writer observes a different published context")
    publish_backtest_definition(writer_client, run_id, definition, keeper=keeper,
        lease=definition_lease, journal_price_contract=journal_price_contract,
        expected_journal_price_contract_hash=expected_journal_price_contract_hash,
        broker_contract=broker_contract, expected_broker_contract_hash=expected_broker_contract_hash)
    require_owners()
    def verify_broker(cold_client):
        persisted_broker = read_broker_contract(cold_client, run_id=run_id,
            content_hash=expected_broker_contract_hash, liquidity_plan_hash=liquidity_plan_hash,
            initial_cash=definition.initial_cash)
        persisted_broker.require_config(selected_simulation, run_id=run_id,
            content_hash=expected_broker_contract_hash, liquidity_plan_hash=liquidity_plan_hash)
    _read_cold_pair(read_client, terminal_client, verify_broker)
    require_owners()
    owner.attest_genesis(configuration_hash=run["configuration_hash"],
        market_plan_token=run["market_plan_token"], code_hash=run["code_hash"])
    token = _verified_v4_token(published, run_id=run_id, account_ids=runtime_config.account_ids,
        configuration_hash=run["configuration_hash"], market_plan_token=run["market_plan_token"],
        certificate=certificate)
    return assemble_fixed_v4_journal(read_client, writer_client, terminal_client, token,
        attempt_id=attempt_id, expected_config=config, fixed_market_parent_plan=fixed_market_parent_plan,
        fixed_market_execution_plan=fixed_market_execution_plan, expected_market_start=definition.session_start,
        writer_factory=writer_factory, batch_size=batch_size, queue_capacity=queue_capacity,
        drawdown_authority=drawdown, exact_oms_prices=True, journal_price_contract=journal_price_contract,
        expected_journal_price_contract_hash=expected_journal_price_contract_hash,
        v4_preflight_seal=seal if writer_factory is ArteJournalWriter else None,
        published_context_seal=_V4PublishedContextSeal(published,
            (read_client, writer_client, terminal_client), _V4_CONTEXT_SECRET))


def assemble_resumed_program_v4_journal(read_client, writer_client, terminal_client, *,
        recovery_evidence, runtime_config, release, fixed_market_parent_plan,
        fixed_market_execution_plan, journal_price_contract, broker_contract,
        oms_lineages, protection_history, attempt_id, projection_certifier,
        writer_factory=ArteJournalWriter, batch_size=1024, queue_capacity=8):
    """Adopt existing programme facts into the shared fenced writer lane.

    Caller-owned connections and replacement lease must already be authorized.
    This synchronous composition belongs in the existing owned worker. It
    neither publishes a new context nor starts actors or admits a cold run.
    """
    from .program_recovery_evidence import ProgramRecoveryEvidence
    from .program_saved_definition import _freeze, ProgramSavedDefinitionEvidence
    from .program_checkpoint_recovery import ProgramFinancialCheckpoint
    from .backtest_fixed_running_anchor import cold_verify_v4_resume_anchor
    from .backtest_fixed_journal_bootstrap import _assemble_v4_writer_lane
    from src.trading_runtime.arte_backtest_definition import load_backtest_definition
    from src.trading_runtime.backtest_journal_price_contract import read_journal_price_contract
    from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
    from src.trading_runtime.arte_journal_reader import CompleteProtectionHistory
    from src.trading_runtime.arte_oms_projection import RecoveredStrategyOneOmsLineage
    from src.trading_runtime.runtime import typed_run_config_payload
    from src.trading_runtime.columnar_strategy_release import ProgramRelease
    from src.trading_runtime.runtime import RunConfig
    from .replay_run_service import _simulation_config, ReplayRunDefinition
    from .backtest_market_data import ExecutionInterval
    from itertools import islice
    if type(recovery_evidence) is not ProgramRecoveryEvidence:
        raise ValueError("Programme resumed journal requires joined cold evidence")
    evidence, financial = recovery_evidence.definition, recovery_evidence.financial
    if type(evidence) is not ProgramSavedDefinitionEvidence or type(financial) is not ProgramFinancialCheckpoint:
        raise ValueError("Programme resumed journal requires typed definition and financial evidence")
    definition, prefix = recovery_evidence.run_definition, evidence.prefix
    lineages = tuple(islice(oms_lineages, 4097))
    owner = getattr(writer_client, "backtest_v4_lease", None)
    dispatch = getattr(writer_client, "typed_insert_dispatch", None)
    if (type(runtime_config) is not RunConfig or runtime_config.mode is not RunMode.BACKTEST
            or type(definition) is not ReplayRunDefinition
            or type(release) is not ProgramRelease or type(journal_price_contract) is not BacktestJournalPriceContract
            or type(broker_contract) is not BacktestBrokerContract
            or type(prefix) is not V4CommittedPrefix or prefix.status != "running"
            or financial.prefix != prefix or runtime_config.run_id != prefix.run_id
            or type(owner) is not BacktestV4KeeperLease or owner.run_id != prefix.run_id
            or owner.epoch < 2 or not isinstance(dispatch, TypedInsertDispatch)
            or dispatch.keeper is not owner.owner._session.client
            or getattr(writer_client, "typed_insert_strict", False) is not True
            or len({id(read_client), id(writer_client), id(terminal_client)}) != 3
            or any(getattr(client, "program_decision_profile", False) is not True
                   for client in (read_client, writer_client, terminal_client))
            or type(batch_size) is not int or not 1 <= batch_size <= MAX_V4_COMMIT_EVENTS
            or type(queue_capacity) is not int or not 1 <= queue_capacity <= 64
            or not callable(projection_certifier) or not callable(writer_factory)
            or type(protection_history) is not CompleteProtectionHistory
            or protection_history.run_id != prefix.run_id
            or protection_history.through_sequence != prefix.last_sequence
            or protection_history.committed_batch_ids != prefix.batch_ids
            or len(lineages) > 4096
            or any(type(row) is not RecoveredStrategyOneOmsLineage
                   or row.through_sequence != prefix.last_sequence
                   or row.source_intent.source_batch is None for row in lineages)):
        raise ValueError("Programme resumed journal lacks bounded owned cold authorities")
    _validate_plans(fixed_market_parent_plan, fixed_market_execution_plan)
    config = typed_run_config_payload(runtime_config)
    configuration_hash = _configuration_hash(definition)
    month = date.fromisoformat(evidence.context["run_month"])
    prepared_definition = prepare_backtest_definition(prefix.run_id, definition, run_month=month)
    if (config != {key: evidence.context.get(key) for key in config}
            or not str(evidence.revision.get("revision_id") or "").startswith("strategy-configuration:")
            or _freeze(definition.configuration_revision) != evidence.revision
            or runtime_config.account_ids != tuple(evidence.context["account_ids"])
            or configuration_hash != evidence.context["configuration_hash"]
            or fixed_market_parent_plan.token != evidence.context["market_plan_token"]
            or release.header["code_hash"] != evidence.context["code_hash"]
            or runtime_config.strategy_id != release.header["strategy_id"]
            or runtime_config.strategy_revision != release.header["strategy_number"]
            or release.header["content_hash"] != definition.configuration_revision["payload"]["strategy"]["program_release_hash"]):
        raise ValueError("Programme resumed journal differs from saved native settings")
    journal_price_contract.require_scope(run_id=prefix.run_id, run_month=month,
        configuration_hash=configuration_hash, content_hash=journal_price_contract.row["content_hash"])
    simulation = replace(_simulation_config(definition),
        liquidity_resolution_ms=ExecutionInterval.parse(definition.execution_interval).milliseconds)
    broker_contract.require_config(simulation, run_id=prefix.run_id,
        content_hash=broker_contract.row["content_hash"], liquidity_plan_hash=broker_contract.row["liquidity_plan_hash"])
    def verify_cold(client):
        from src.trading_runtime.rule_decision_writer_preflight import program_checkpoint_storage_contracts
        _v4_cold_reader_preflight(client)
        storage_preflight(client, tables=(PRICE_CONTRACT, BROKER, *program_checkpoint_storage_contracts()))
        context = load_typed_run_context(client, prefix.run_id)
        if (_freeze(context) != evidence.context
                or _freeze(load_backtest_definition(client, prefix.run_id, run_context=context)) != evidence.saved):
            raise RuntimeError("Programme resumed definition changed across cold readers")
        persisted = read_journal_price_contract(client, run_id=prefix.run_id, run_month=month,
            configuration_hash=configuration_hash, content_hash=journal_price_contract.row["content_hash"])
        persisted_broker = read_broker_contract(client, run_id=prefix.run_id,
            content_hash=broker_contract.row["content_hash"], liquidity_plan_hash=broker_contract.row["liquidity_plan_hash"],
            initial_cash=definition.initial_cash)
        if persisted.row != journal_price_contract.row or persisted_broker.row != broker_contract.row:
            raise RuntimeError("Programme resumed contracts changed across cold readers")
    _read_cold_pair(read_client, terminal_client, verify_cold)
    def verify_anchor(client):
        anchor = cold_verify_v4_resume_anchor(client, dispatch=dispatch, lease=owner,
            run_id=prefix.run_id, plan=fixed_market_parent_plan, configuration_hash=configuration_hash,
            account_ids=runtime_config.account_ids, code_hash=evidence.context["code_hash"])
        if ((anchor.journal_sequence, anchor.batch_id, anchor.source_cursor, anchor.completed_at)
                != (prefix.last_sequence, prefix.last_batch_id, prefix.source_cursor, financial.completed_at)):
            raise RuntimeError("Programme resumed journal differs from the joined checkpoint")
        return anchor
    # Each barrier is exclusive for this run; independent SELECTs above may
    # overlap, but the two ownership attestations must settle sequentially.
    anchors = tuple(verify_anchor(client) for client in (read_client, terminal_client))
    if anchors[0] != anchors[1] or _freeze(load_typed_run_context(writer_client, prefix.run_id)) != evidence.context:
        raise RuntimeError("Programme resumed journal differs across principals")
    _v4_preflight(writer_client)
    certificate = projection_certifier()
    token = _verified_v4_token(dict(evidence.context), run_id=prefix.run_id,
        account_ids=runtime_config.account_ids, configuration_hash=configuration_hash,
        market_plan_token=fixed_market_parent_plan.token, certificate=certificate)
    owner.assert_current()
    if (_configuration_hash(definition) != configuration_hash
            or prepare_backtest_definition(prefix.run_id, definition, run_month=month) != prepared_definition):
        raise RuntimeError("Programme resumed settings changed before writer adoption")
    assembly = _assemble_v4_writer_lane(writer_client, token, attempt_id=attempt_id,
        expected_config=config, fixed_market_parent_plan=fixed_market_parent_plan,
        fixed_market_execution_plan=fixed_market_execution_plan, expected_market_start=definition.session_start,
        writer_factory=writer_factory, batch_size=batch_size, queue_capacity=queue_capacity,
        initial_sequence=prefix.last_sequence, prior_batch_id=prefix.last_batch_id,
        source_cursor=prefix.source_cursor, exact_oms_prices=True)
    try:
        assembly.journal.restore_verified_portfolio_admissions(lineages)
        assembly.journal.restore_committed_records(protection_history.records)
        assembly.publisher.restore_verified_oms_sources(lineages, protection_history)
        owner.assert_current()
        if (_configuration_hash(definition) != configuration_hash
                or prepare_backtest_definition(prefix.run_id, definition, run_month=month) != prepared_definition):
            raise RuntimeError("Programme resumed settings changed during writer adoption")
    except BaseException:
        try:
            assembly.writer.close()
        finally:
            assembly.journal.close()
        raise
    return assembly, anchors[0]
