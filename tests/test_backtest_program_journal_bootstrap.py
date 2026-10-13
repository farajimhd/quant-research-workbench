"""Programme handoff ordering with real in-memory Keeper ownership."""
from datetime import datetime, timezone
from types import SimpleNamespace
from hashlib import sha256

import pytest

from src.backend import backtest_program_journal_bootstrap as bootstrap
from src.backend.backtest_v4_run_context import historical_runtime_config
from src.backend.backtest_v4_keeper_lease import BacktestV4KeeperLease
from src.trading_runtime.keeper_session import ManagedKeeperSession
from src.trading_runtime.keeper_ownership import KeeperOwnershipCoordinator
from src.trading_runtime.arte_typed_insert_dispatch import TypedInsertDispatch
from src.trading_runtime.backtest_journal_price_contract import (
    BacktestJournalPriceContract, project_journal_price_contract, EXACT_PRICE_CODEC)
from tests.test_live_signal_completion_keeper import FakeKazoo
from tests.test_arte_backtest_definition import _definition
from tests.test_columnar_program_coordinator import inputs
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.backtest_broker_contract import BacktestBrokerContract, project_broker_contract
from src.backend.replay_run_service import _simulation_config
from dataclasses import replace
from threading import Barrier, get_ident


@pytest.mark.parametrize("failure", [None, "context", "cursor", "certificate", "restore",
    "settings_before", "settings_during"])
def test_resumed_programme_composition_seeds_existing_prefix_without_publication(monkeypatch, failure):
    """Control-read/writer seams only; shared readers verify their own facts."""
    from src.backend.program_saved_definition import ProgramSavedDefinitionEvidence, _freeze
    from src.backend.program_checkpoint_recovery import ProgramFinancialCheckpoint
    from src.backend.program_recovery_evidence import ProgramRecoveryEvidence
    from src.backend import backtest_fixed_journal_bootstrap as shared
    from src.backend import backtest_fixed_running_anchor as running
    from src.trading_runtime import arte_backtest_definition as definitions
    from src.trading_runtime import backtest_journal_price_contract as prices
    from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
    from src.trading_runtime.arte_journal_reader import CompleteProtectionHistory
    run_id = "00000000-0000-0000-0000-000000000001"
    release, _, _, _ = inputs(entry=True)
    payload = dict(strategy=dict(strategy_id=release.header["strategy_id"],
        revision=release.header["strategy_number"], program_release_hash=release.header["content_hash"]),
        run_plan=dict(run_plan_id="programme-plan"))
    digest = sha256(canonical_json(payload).encode()).hexdigest()
    definition = _definition(configuration_revision=dict(revision_id="strategy-configuration:" + run_id,
        content_hash=digest, payload=payload))
    runtime = historical_runtime_config(mode=definition.mode, account_ids=("DU1",),
        anchor_date=definition.session_date, run_id=run_id, configuration=payload)
    plan = SimpleNamespace(token="c" * 64, build_id="build", definition_hash="d" * 64,
        sessions=(definition.session_date.isoformat(),), tickers=("ABC",),
        execution_interval="100ms", required_resolutions_ms=(100,))
    started = datetime(2026, 9, 1, tzinfo=timezone.utc)
    run, config = bootstrap.program_v4_context_rows(runtime, release=release,
        expected_release_hash=release.header["content_hash"], execution_interval=definition.execution_interval,
        configuration_hash=digest, code_hash=release.header["code_hash"], market_plan_token=plan.token, started_at=started)
    context = {**run, **config, "account_ids": runtime.account_ids}
    saved = {"definition": {"saved": True}}
    prefix = V4CommittedPrefix(run_id, 40, "00000000-0000-0000-0000-000000000004", "cursor", "running",
        ("00000000-0000-0000-0000-000000000004",))
    at = definition.requested_start
    evidence = ProgramRecoveryEvidence(ProgramSavedDefinitionEvidence(prefix, _freeze(context), _freeze(saved),
        _freeze(definition.configuration_revision)),
        ProgramFinancialCheckpoint(prefix, object(), object(), object(), at), definition)
    history = CompleteProtectionHistory(run_id, prefix.last_sequence, prefix.batch_ids, ())
    kazoo = FakeKazoo()
    kazoo.add_listener = kazoo.remove_listener = lambda _: None
    kazoo.stop = kazoo.close = lambda: None
    session = ManagedKeeperSession(kazoo)
    session._on_state("CONNECTED")
    first = BacktestV4KeeperLease.acquire(session, run_id=run_id, owner_id="original")
    first.attest_genesis(configuration_hash=digest, market_plan_token=plan.token, code_hash=release.header["code_hash"])
    first.release()
    owner = BacktestV4KeeperLease.acquire(session, run_id=run_id, owner_id="replacement")
    reader, terminal = (SimpleNamespace(program_decision_profile=True) for _ in range(2))
    writer = SimpleNamespace(program_decision_profile=True, typed_insert_strict=True,
        typed_insert_dispatch=TypedInsertDispatch(kazoo), backtest_v4_lease=owner)
    price_row = project_journal_price_contract(run_id=run_id, run_month=started.date().replace(day=1),
        configuration_hash=digest, price_codec=EXACT_PRICE_CODEC)
    price = BacktestJournalPriceContract(price_row)
    broker = BacktestBrokerContract(project_broker_contract(run_id, _simulation_config(definition),
        liquidity_plan_hash=plan.token), definition.initial_cash)
    phases = []
    monkeypatch.setattr(bootstrap, "_v4_cold_reader_preflight", lambda _: None)
    monkeypatch.setattr(bootstrap, "storage_preflight", lambda *a, **k: None)
    monkeypatch.setattr(bootstrap, "_v4_preflight", lambda _: None)
    monkeypatch.setattr(bootstrap, "load_typed_run_context", lambda *_:
        {**context, "code_hash": "e" * 64} if failure == "context" else context)
    monkeypatch.setattr(definitions, "load_backtest_definition", lambda *a, **k: saved)
    monkeypatch.setattr(prices, "read_journal_price_contract", lambda *a, **k: price)
    monkeypatch.setattr(bootstrap, "read_broker_contract", lambda *a, **k: broker)
    anchor = SimpleNamespace(journal_sequence=40, batch_id=prefix.last_batch_id,
        source_cursor="cursor", completed_at=at)
    def cold(client, **scope):
        phases.append("anchor")
        owner.assert_current()
        owner.assert_genesis(configuration_hash=digest, market_plan_token=plan.token, code_hash=release.header["code_hash"])
        assert scope["lease"] is owner and scope["code_hash"] == release.header["code_hash"]
        if failure == "settings_before": payload["strategy"]["program_release_hash"] = "e" * 64
        return SimpleNamespace(**{**vars(anchor), "journal_sequence": 41}) if failure == "cursor" else anchor
    monkeypatch.setattr(running, "cold_verify_v4_resume_anchor", cold)
    def restore(*args):
        phases.append("admissions")
        if failure == "restore": raise ValueError("restore failed")
    assembly = SimpleNamespace(journal=SimpleNamespace(restore_verified_portfolio_admissions=restore,
        restore_committed_records=lambda _: phases.append("records"), close=lambda: phases.append("journal-close")),
        publisher=SimpleNamespace(restore_verified_oms_sources=lambda *a: phases.append("sources")),
        writer=SimpleNamespace(close=lambda: phases.append("writer-close")))
    def assemble(client, token, **scope):
        assert scope["initial_sequence"] == 40 and scope["prior_batch_id"] == prefix.last_batch_id
        assert scope["source_cursor"] == "cursor" and scope["exact_oms_prices"] is True
        phases.append("writer")
        if failure == "settings_during": payload["strategy"]["program_release_hash"] = "e" * 64
        return assembly
    monkeypatch.setattr(shared, "_assemble_v4_writer_lane", assemble)
    monkeypatch.setattr(bootstrap, "publish_fixed_run_context", lambda *a, **k: pytest.fail("fresh context publication"))
    monkeypatch.setattr(bootstrap, "publish_backtest_definition", lambda *a, **k: pytest.fail("fresh definition publication"))
    try:
        options = dict(recovery_evidence=evidence, runtime_config=runtime, release=release,
            fixed_market_parent_plan=plan, fixed_market_execution_plan=plan,
            journal_price_contract=price, broker_contract=broker, oms_lineages=(), protection_history=history,
            attempt_id="00000000-0000-0000-0000-000000000005",
            projection_certifier=lambda: "invalid" if failure == "certificate" else "f" * 64)
        if failure:
            with pytest.raises((ValueError, RuntimeError, ExceptionGroup)):
                bootstrap.assemble_resumed_program_v4_journal(reader, writer, terminal, **options)
            assert ("writer" in phases) is (failure in {"restore", "settings_during"})
            if failure in {"restore", "settings_during"}:
                assert phases[-2:] == ["writer-close", "journal-close"]
        else:
            assert bootstrap.assemble_resumed_program_v4_journal(reader, writer, terminal, **options) == (assembly, anchor)
            assert phases == ["anchor", "anchor", "writer", "admissions", "records", "sources"]
        owner.assert_current()
    finally:
        owner.release()
        session.close()


@pytest.mark.parametrize("resolution", [100, 1000])
@pytest.mark.parametrize("failure", [None, "storage", "certificate", "context", "definition", "assembly", "context_mutation", "claim_lost", "broker_readback"])
def test_programme_publication_precedes_writer_and_preserves_same_run_on_failure(monkeypatch, failure, resolution):
    run_id = "00000000-0000-0000-0000-000000000001"
    release, _, _, _ = inputs(entry=True)
    strategy = dict(strategy_id=release.header["strategy_id"], revision=release.header["strategy_number"])
    configuration = dict(strategy=strategy, run_plan=dict(run_plan_id="programme-plan"))
    configuration_hash = sha256(canonical_json(configuration).encode("utf-8")).hexdigest()
    definition = _definition(configuration_revision=dict(revision_id="revision-1",
        content_hash=configuration_hash, payload=configuration))
    runtime = historical_runtime_config(mode=definition.mode, account_ids=("DU1",),
        anchor_date=definition.session_date, run_id=run_id, configuration=configuration)
    plan = SimpleNamespace(token="c" * 64, build_id="build-1", definition_hash="d" * 64,
        sessions=(definition.session_date.isoformat(),), tickers=("ABCD", "EFGH"),
        execution_interval="100ms", required_resolutions_ms=(100,))
    kazoo = FakeKazoo()
    kazoo.add_listener = lambda listener: None
    kazoo.remove_listener = lambda listener: None
    kazoo.stop = lambda: None
    kazoo.close = lambda: None
    session = ManagedKeeperSession(kazoo)
    session._on_state("CONNECTED")
    owner = BacktestV4KeeperLease.acquire(session, run_id=run_id, owner_id="programme-owner")
    keeper = KeeperOwnershipCoordinator(kazoo)
    definition_lease = keeper.acquire_portfolio_admission_lease(
        f"backtest-definition:{run_id}", owner_id="definition-owner", ttl_seconds=300)
    assert definition_lease is not None
    dispatch = TypedInsertDispatch(kazoo)
    context = SimpleNamespace(typed_insert_dispatch=dispatch, typed_insert_strict=True)
    writer = SimpleNamespace(typed_insert_dispatch=TypedInsertDispatch(kazoo),
        typed_insert_strict=True, program_decision_profile=True, backtest_v4_lease=owner)
    reader, terminal = (SimpleNamespace(program_decision_profile=True) for _ in range(2))
    started = datetime(2026, 9, 1, tzinfo=timezone.utc)
    price_row = project_journal_price_contract(run_id=run_id,
        run_month=started.date().replace(day=1), configuration_hash=configuration_hash, price_codec=EXACT_PRICE_CODEC)
    selected_simulation = replace(_simulation_config(definition), liquidity_resolution_ms=resolution)
    broker_row = project_broker_contract(run_id, selected_simulation, liquidity_plan_hash="c" * 64)
    broker_contract = BacktestBrokerContract(broker_row, definition.initial_cash)
    phases, published = [], {}
    monkeypatch.setattr(bootstrap, "fixed_backtest_v2_preflight", lambda client: phases.append("context-preflight"))
    monkeypatch.setattr(bootstrap, "_v4_cold_reader_preflight", lambda client: phases.append("reader-preflight"))
    def storage(client, *, tables):
        from src.trading_runtime.rule_decision_writer_preflight import program_checkpoint_storage_contracts
        assert {table.name for table in tables} == {PRICE_CONTRACT.name, BROKER.name} | {
            table.name for table in program_checkpoint_storage_contracts()}
        phases.append("price-preflight")
        if failure == "storage":
            raise ValueError("synthetic missing checkpoint storage")
    from src.trading_runtime.backtest_journal_price_contract import PRICE_CONTRACT
    from src.trading_runtime.columnar_strategy_schema import BROKER
    monkeypatch.setattr(bootstrap, "storage_preflight", storage)
    monkeypatch.setattr(bootstrap, "_v4_preflight", lambda client: phases.append("writer-preflight"))

    def certificate():
        phases.append("certificate")
        return "invalid" if failure == "certificate" else "f" * 64

    def publish_context(*args, **kwargs):
        phases.append("context")
        if failure == "context":
            raise RuntimeError("synthetic context failure")
        published.update(kwargs["run"], **kwargs["config"], account_ids=kwargs["account_ids"])
        if failure == "context_mutation":
            definition.configuration_revision["payload"]["strategy"]["parameters"] = {"changed": True}
        if failure == "claim_lost":
            assert keeper.release_portfolio_admission_lease(f"backtest-definition:{run_id}",
                owner_id=definition_lease["owner_id"], epoch=definition_lease["epoch"])
        return dict(published)

    def publish_definition(*args, **kwargs):
        phases.append("definition")
        assert args[1] == run_id and kwargs["journal_price_contract"].row == price_row
        assert kwargs["broker_contract"].row == broker_row
        if failure == "definition":
            raise RuntimeError("synthetic definition failure")

    def assemble(*args, **kwargs):
        phases.append("assembly")
        assert kwargs["exact_oms_prices"] is True
        assert kwargs["expected_journal_price_contract_hash"] == price_row["content_hash"]
        assert args[3].run_id == run_id
        if failure == "assembly":
            raise RuntimeError("synthetic assembly failure")
        return "assembled"

    monkeypatch.setattr(bootstrap, "publish_fixed_run_context", publish_context)
    monkeypatch.setattr(bootstrap, "load_typed_run_context", lambda *args: dict(published))
    monkeypatch.setattr(bootstrap, "publish_backtest_definition", publish_definition)
    monkeypatch.setattr(bootstrap, "assemble_fixed_v4_journal", assemble)
    def broker_readback(client, **scope):
        assert client is reader or client is terminal
        phases.append("broker-readback")
        if failure == "broker_readback":
            raise ValueError("synthetic broker readback failure")
        assert scope["content_hash"] == broker_row["content_hash"]
        return broker_contract
    monkeypatch.setattr(bootstrap, "read_broker_contract", broker_readback)
    options = dict(runtime_config=runtime, definition=definition, release=release,
        expected_release_hash=release.header["content_hash"], code_hash=release.header["code_hash"],
        started_at=started, fixed_market_parent_plan=plan, fixed_market_execution_plan=plan,
        journal_price_contract=BacktestJournalPriceContract(price_row),
        expected_journal_price_contract_hash=price_row["content_hash"], keeper=keeper,
        broker_contract=broker_contract, expected_broker_contract_hash=broker_row["content_hash"],
        liquidity_plan_hash="c" * 64,
        definition_lease=definition_lease, attempt_id="00000000-0000-0000-0000-000000000002",
        projection_certifier=certificate, writer_factory=lambda *args, **kwargs: None)
    try:
        if failure:
            with pytest.raises((RuntimeError, ValueError, ExceptionGroup)):
                bootstrap.publish_and_assemble_program_v4_journal(context, reader, writer, terminal, **options)
        else:
            assert bootstrap.publish_and_assemble_program_v4_journal(context, reader, writer, terminal, **options) == "assembled"
        publication = [phase for phase in phases if phase in ("context", "definition", "assembly")]
        expected = ["context", "definition", "assembly"]
        if failure in ("certificate", "storage"):
            expected = []
        elif failure in ("context", "definition"):
            expected = expected[:expected.index(failure) + 1]
        elif failure in ("context_mutation", "claim_lost"):
            expected = ["context"]
        elif failure == "broker_readback":
            expected = ["context", "definition"]
        assert publication == expected
        owner.assert_current()  # Handoff never silently replaces or releases this run.
    finally:
        keeper.release_portfolio_admission_lease(f"backtest-definition:{run_id}",
            owner_id=definition_lease["owner_id"], epoch=definition_lease["epoch"])
        owner.release()
        session.close()


@pytest.mark.parametrize("failures", [0, 1, 2])
def test_independent_cold_reads_overlap_and_both_settle(failures):
    clients = (object(), object())
    barrier = Barrier(2)
    visited, finished, threads = [], [], []
    caller = get_ident()
    def operation(client):
        ordinal = clients.index(client)
        visited.append(ordinal)
        threads.append(get_ident())
        barrier.wait(timeout=2)
        finished.append(ordinal)
        if ordinal < failures:
            raise ValueError(f"cold reader {ordinal} rejected")
        return ordinal
    if failures == 1:
        with pytest.raises(ValueError, match="reader 0 rejected"):
            bootstrap._read_cold_pair(*clients, operation)
    elif failures == 2:
        with pytest.raises(ExceptionGroup) as error:
            bootstrap._read_cold_pair(*clients, operation)
        assert len(error.value.exceptions) == 2
    else:
        assert bootstrap._read_cold_pair(*clients, operation) == (0, 1)
    assert sorted(visited) == sorted(finished) == [0, 1]
    assert len(set(threads)) == 2 and caller not in threads
    with pytest.raises(ValueError, match="distinct"):
        bootstrap._read_cold_pair(clients[0], clients[0], operation)
