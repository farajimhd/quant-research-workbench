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
    for client in (read_client, terminal_client):
        _v4_cold_reader_preflight(client)
        storage_preflight(client, tables=(PRICE_CONTRACT, BROKER))
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
    for cold_client in (read_client, terminal_client):
        persisted_broker = read_broker_contract(cold_client, run_id=run_id,
            content_hash=expected_broker_contract_hash, liquidity_plan_hash=liquidity_plan_hash,
            initial_cash=definition.initial_cash)
        persisted_broker.require_config(selected_simulation, run_id=run_id,
            content_hash=expected_broker_contract_hash, liquidity_plan_hash=liquidity_plan_hash)
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
