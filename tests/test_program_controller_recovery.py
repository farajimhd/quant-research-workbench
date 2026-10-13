"""Controller custody composition with explicit financial/read-proof seams."""
import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from hashlib import sha256
from types import SimpleNamespace

import pytest

from src.backend import program_controller_recovery as adoption
from src.backend.replay_run_service import ReplayRunController
from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.backend.backtest_typed_publisher import BacktestTypedJournalPublisher
from src.backend.backtest_fixed_journal_bootstrap import FixedJournalAssembly, FixedV4JournalPreflightToken
from src.backend.backtest_fixed_running_anchor import FixedRunningPrefixAnchor
from src.backend.backtest_v4_keeper_lease import BacktestV4KeeperLease
from src.backend.program_saved_definition import ProgramSavedDefinitionEvidence
from src.backend.program_recovery_evidence import ProgramRecoveryEvidence
from src.backend.program_checkpoint_recovery import ProgramFinancialCheckpoint
from src.backend.program_policy_recovery import RecoveredProgramPolicy, _RECOVERY_PROOF, program_policy_fingerprint
from src.backend.signal_stream_runtime_service import SignalStreamRuntime
from src.backend.qmd_columnar_population import CertifiedPopulationWindow
from src.backend.backtest_market_data import market_day_boundary
from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
from src.trading_runtime.arte_broker_match_snapshot import project_shared_broker_snapshot
from src.trading_runtime.backtest_broker_contract import BacktestBrokerContract, project_broker_contract
from src.trading_runtime.runtime import typed_run_config_payload
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.columnar_program_actor import AssignedProgramStrategy
from tests.test_program_controller_runtime import definition_and_actor
from tests.test_backtest_v4_keeper_lease import _session, RUN_ID


@pytest.mark.parametrize("failure", [None, "financial", "positions", "source", "publisher", "concurrent"])
def test_adoption_transfers_only_after_common_native_scope_is_qualified(tmp_path, monkeypatch, failure):
    definition, actor = definition_and_actor()
    revision = deepcopy(definition.configuration_revision)
    revision["revision_id"] = "strategy-configuration:" + RUN_ID
    revision["content_hash"] = sha256(canonical_json(revision["payload"]).encode()).hexdigest()
    definition = replace(definition, configuration_revision=revision,
        market_data_plan={**definition.market_data_plan, "token": "c" * 64})
    controller = ReplayRunController(definition, run_id=RUN_ID, runtime_root=tmp_path,
        program_strategy=actor, program_liquidity_resolution_ms=100)
    staged = ReplayRunController(definition, run_id=RUN_ID, runtime_root=tmp_path,
        program_strategy=AssignedProgramStrategy(actor.program_release, actor.assignments(), actor._listings),
        program_liquidity_resolution_ms=100)
    journal = BacktestMemoryJournal(run_id=RUN_ID, initial_sequence=10)
    staged._journal = journal
    session = _session()
    session.client.remove_listener = lambda _: None
    session.client.stop = session.client.close = lambda: None
    first = BacktestV4KeeperLease.acquire(session, run_id=RUN_ID, owner_id="original")
    first.release()
    lease = BacktestV4KeeperLease.acquire(session, run_id=RUN_ID, owner_id="replacement")
    writer = SimpleNamespace(_client=SimpleNamespace(backtest_v4_lease=lease), journal_profile="backtest_v4", run_id=RUN_ID)
    publisher = BacktestTypedJournalPublisher.__new__(BacktestTypedJournalPublisher)
    publisher.journal, publisher.writer = journal, writer
    publisher._sequence, publisher._batch_id, publisher._source_cursor = 10, "batch", "cursor"
    publisher._error = publisher._task = publisher._terminal_task = None
    publisher.exact_oms_prices = True
    token = FixedV4JournalPreflightToken(RUN_ID, (actor.assignments()[0].account_id,),
        definition.session_date.replace(day=1), revision["content_hash"], "c" * 64, "d" * 64)
    assembly = FixedJournalAssembly(token, journal, writer, publisher, None)
    population = CertifiedPopulationWindow.__new__(CertifiedPopulationWindow)
    object.__setattr__(population, "source_revision", "source")
    source = SignalStreamRuntime()
    source._typed_scope = (revision["revision_id"], "source", {})
    calls = []

    async def run():
        await staged._initialize_runtime(record_configuration=False, record_lifecycle=False)
        runtime = staged._runtime
        # Cold-financial equivalence and receipt reads are deliberate seams;
        # the native financial/OMS/broker tests cover those authorities.
        runtime._typed_oms_recovered = True
        runtime.portfolio._typed_backtest_recovery = True
        sequence = journal.latest_sequence(RUN_ID)
        journal.mark_fenced(sequence)
        publisher._sequence = sequence
        at = definition.requested_start + timedelta(seconds=1)
        runtime.last_event_time = at
        prefix = V4CommittedPrefix(RUN_ID, sequence, "batch", "cursor", "running", ("batch",))
        boundary_ms = int((at - market_day_boundary(definition.session_date, 0)).total_seconds() * 1000)
        anchor = FixedRunningPrefixAnchor(RUN_ID, "batch", sequence, "cursor", definition.session_date.isoformat(),
            boundary_ms, boundary_ms // 100, at, None)
        broker_contract = BacktestBrokerContract(project_broker_contract(RUN_ID, runtime.broker.config,
            liquidity_plan_hash="a" * 64), definition.initial_cash)
        broker = project_shared_broker_snapshot(run_id=RUN_ID, session_date=definition.session_date,
            checkpoint_sequence=sequence, boundary_ms=boundary_ms, state=runtime.broker.checkpoint_state(),
            simulation_config=runtime.broker.config, broker_contract=broker_contract)
        financial = ProgramFinancialCheckpoint(prefix, broker, object(), object(), at)
        evidence = ProgramRecoveryEvidence(ProgramSavedDefinitionEvidence(prefix,
            dict(account_ids=runtime.config.account_ids, started_at=at.isoformat(),
                configuration_hash=revision["content_hash"], code_hash=actor.program_release.header["code_hash"]),
            {}, revision), financial, definition)
        source._typed_cursor, source._typed_prefix, source._typed_cutoffs = (1, "b" * 64, at), ["b" * 64], [at]
        packet_actor = AssignedProgramStrategy(actor.program_release, actor.assignments(), actor._listings)
        packet = RecoveredProgramPolicy(packet_actor, sequence, 0, 0, {}, RUN_ID, ("b" * 64,),
            program_policy_fingerprint(packet_actor), _RECOVERY_PROOF)
        publisher.expected_config = typed_run_config_payload(runtime.config)
        synchronized = object()
        def qualify(*args, **scope):
            calls.append("financial")
            assert controller._program_startup_active
            if failure == "concurrent":
                with pytest.raises(RuntimeError, match="startup already owns"):
                    asyncio.run(controller.adopt_program_cold_runtime(**kwargs))
            assert args[:3] == (runtime, financial.portfolio, prefix)
            if failure == "financial": raise ValueError("financial failed")
            return synchronized
        def positions(actual, template, **scope):
            calls.append("positions")
            assert actual is packet and template is actor and scope["portfolio_recovery"] is synchronized
            assert set(scope["broker_positions"]) == set(runtime.config.account_ids)
            if failure == "positions": raise ValueError("positions failed")
            if failure == "source": source._typed_prefix = ["c" * 64]
            if failure == "publisher": publisher._error = RuntimeError("publisher failed")
        monkeypatch.setattr(adoption, "qualify_program_financial_runtime", qualify)
        monkeypatch.setattr(adoption, "verify_recovered_program_positions", positions)
        try:
            kwargs = dict(assembly=assembly, evidence=evidence, runtime=runtime, packet=packet,
                source=source, population=population, oms_image=object(), anchor=anchor,
                lease=lease, keeper_session=session, broker_contract=broker_contract)
            if failure not in (None, "concurrent"):
                with pytest.raises((ValueError, RuntimeError), match="failed|source advanced|idle scope"):
                    await controller.adopt_program_cold_runtime(**kwargs)
                assert controller._runtime is None and controller._journal is None
                assert controller._fixed_v4_lease is None and controller._task is None
            else:
                proof = await controller.adopt_program_cold_runtime(**kwargs)
                assert proof.controller is controller and proof.packet is packet
                assert controller._runtime is runtime and controller._journal is journal
                assert runtime.strategy is controller._program_strategy is packet_actor
                assert controller._source_cursor["boundary_ms"] == boundary_ms
                assert controller._fixed_v4_lease is lease and controller.current_time == at
                assert controller._task is None and controller.status == "created"
                assert controller.created_at == at
            lease.assert_current()
            assert not controller._program_startup_active
        finally:
            await runtime.close_background_tasks()
    try:
        asyncio.run(run())
    finally:
        journal.close()
        lease.release()
        session.close()
    assert calls == (["financial"] if failure == "financial" else ["financial", "positions"])
