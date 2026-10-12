"""Owned startup cancellation and adoption; transport qualification is separate."""
import asyncio
from dataclasses import replace
from hashlib import sha256
from threading import Event, get_ident
from types import SimpleNamespace

import pytest

from src.backend import backtest_program_journal_startup as startup
from src.backend.backtest_fixed_journal_bootstrap import FixedJournalAssembly, FixedV4JournalPreflightToken
from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.backend.backtest_typed_publisher import BacktestTypedJournalPublisher
from src.backend.backtest_v4_run_context import historical_runtime_config
from src.backend.backtest_v4_keeper_lease import BacktestV4KeeperLease
from src.backend.replay_run_service import ReplayRunController, _simulation_config
from src.trading_runtime.keeper_session import ManagedKeeperSession
from src.trading_runtime.keeper_ownership import KeeperOwnershipCoordinator
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.backtest_journal_price_contract import (
    BacktestJournalPriceContract, project_journal_price_contract, EXACT_PRICE_CODEC)
from src.trading_runtime.backtest_broker_contract import BacktestBrokerContract, project_broker_contract
from tests.test_program_controller_runtime import definition_and_actor
from tests.test_live_signal_completion_keeper import FakeKazoo


@pytest.mark.parametrize("case", ["adopt", "cancel_twice", "changed_definition", "changed_policy"])
def test_controller_drains_same_worker_and_only_adopts_unchanged_startup(tmp_path, monkeypatch, case):
    definition, actor = definition_and_actor()
    revision = {**definition.configuration_revision,
        "content_hash": sha256(canonical_json(definition.configuration_revision["payload"]).encode()).hexdigest()}
    definition = replace(definition, configuration_revision=revision,
        market_data_plan={**definition.market_data_plan, "token": "c" * 64},
        causal_v7_plan=dict(token="synthetic-causal-plan", catalog_hash="b" * 64),
        experimental_structure_fingerprint="b" * 64)
    controller = ReplayRunController(definition, runtime_root=tmp_path, program_strategy=actor,
        program_liquidity_resolution_ms=1000)
    runtime = historical_runtime_config(mode=definition.mode,
        configuration=revision["payload"], account_ids=(actor.assignments()[0].account_id,),
        anchor_date=definition.session_date, run_id=controller.run_id)
    kazoo = FakeKazoo()
    kazoo.add_listener = lambda listener: None
    kazoo.remove_listener = lambda listener: None
    kazoo.stop = lambda: None
    kazoo.close = lambda: None
    session = ManagedKeeperSession(kazoo)
    session._on_state("CONNECTED")
    lease = BacktestV4KeeperLease.acquire(session, run_id=controller.run_id, owner_id="startup-owner")
    keeper = KeeperOwnershipCoordinator(kazoo)
    month = controller.created_at.date().replace(day=1)
    price_row = project_journal_price_contract(run_id=controller.run_id, run_month=month,
        configuration_hash=revision["content_hash"], price_codec=EXACT_PRICE_CODEC)
    simulation = replace(_simulation_config(definition), liquidity_resolution_ms=1000)
    broker_row = project_broker_contract(controller.run_id, simulation, liquidity_plan_hash="c" * 64)
    broker_contract = BacktestBrokerContract(broker_row, definition.initial_cash)
    entered, release = Event(), Event()
    closed, threads = [], []
    class Writer:
        run_id = controller.run_id
        run_mode = "backtest"
        journal_profile = "backtest_v4"
        coalesce_batches = False
        max_events_per_commit = 1024
        def close(self):
            closed.append(get_ident())
    writer = Writer()
    journal = BacktestMemoryJournal(run_id=controller.run_id)
    publisher = BacktestTypedJournalPublisher(journal, writer,
        attempt_id="00000000-0000-0000-0000-000000000001", run_month=month, exact_oms_prices=True)
    token = FixedV4JournalPreflightToken(controller.run_id, runtime.account_ids, month,
        revision["content_hash"], "c" * 64, "f" * 64)
    assembly = FixedJournalAssembly(token, journal, writer, publisher, None)
    def bootstrap(*args, **scope):
        threads.append(get_ident())
        entered.set()
        assert release.wait(5)
        return assembly
    monkeypatch.setattr(startup, "publish_and_assemble_program_v4_journal", bootstrap)
    plan = SimpleNamespace(token="c" * 64)
    options = dict(context_client=object(), read_client=object(), terminal_client=object(),
        writer_client=SimpleNamespace(backtest_v4_lease=lease), keeper_session=session, keeper=keeper,
        runtime_config=runtime, expected_release_hash=actor.program_release.header["content_hash"],
        code_hash=actor.program_release.header["code_hash"], fixed_market_parent_plan=plan,
        fixed_market_execution_plan=plan, journal_price_contract=BacktestJournalPriceContract(price_row),
        expected_journal_price_contract_hash=price_row["content_hash"], broker_contract=broker_contract,
        expected_broker_contract_hash=broker_row["content_hash"], liquidity_plan_hash="c" * 64,
        definition_lease={}, attempt_id="00000000-0000-0000-0000-000000000001",
        projection_certifier=lambda: "f" * 64)

    async def run():
        caller = get_ident()
        mismatched = replace(simulation, liquidity_resolution_ms=100)
        mismatched_row = project_broker_contract(controller.run_id, mismatched, liquidity_plan_hash="c" * 64)
        invalid_scopes = [
            {**options, "broker_contract": BacktestBrokerContract(mismatched_row, definition.initial_cash),
             "expected_broker_contract_hash": mismatched_row["content_hash"]},
            {**options, "broker_contract": object()},
            {**options, "journal_price_contract": object()},
            {**options, "runtime_config": object()},
        ]
        for invalid in invalid_scopes:
            with pytest.raises(RuntimeError, match="fresh exclusive"):
                await controller.attach_prepared_program_journal(**invalid)
        controller._stop_requested = True
        with pytest.raises(RuntimeError, match="fresh exclusive"):
            await controller.attach_prepared_program_journal(**options)
        controller._stop_requested = False
        assert not threads and not closed
        assert controller._program_dispatch_failure is None
        task = asyncio.create_task(controller.attach_prepared_program_journal(**options))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            assert controller._program_startup_active
            with pytest.raises(RuntimeError, match="already owns"):
                await controller.attach_prepared_program_journal(**options)
            if case == "cancel_twice":
                task.cancel()
                await asyncio.sleep(0)
                task.cancel()
                await asyncio.sleep(0)
                assert not task.done() and controller._program_startup_active
            elif case == "changed_definition":
                controller.definition = replace(definition, initial_cash=definition.initial_cash + 1)
            elif case == "changed_policy":
                actor._selection_clocks["assignment"] = (definition.session_start, "changed")
            release.set()
            if case == "cancel_twice":
                with pytest.raises(asyncio.CancelledError):
                    await task
            elif case != "adopt":
                with pytest.raises(RuntimeError, match="ownership changed"):
                    await task
            else:
                await task
                assert controller._journal is journal
                assert controller._fixed_keeper_session is session
                assert controller._fixed_v4_lease is lease
                assert not closed
                await controller._initialize_runtime(record_configuration=False, record_lifecycle=False)
                assert controller._runtime.broker.config == simulation
                await controller._runtime.finish()
                await controller._close_fixed_journal()
            assert not controller._program_startup_active
            assert len(closed) == 1
            assert len(threads) == 1
            assert all(thread != caller for thread in [*threads, *closed])
            if case != "adopt":
                assert controller._journal is None
                assert controller._program_dispatch_failure is not None
                with pytest.raises(RuntimeError, match="fresh exclusive"):
                    await controller.attach_prepared_program_journal(**options)
        finally:
            release.set()
            if not task.done():
                task.cancel()
            try:
                await task
            except BaseException:
                pass
    try:
        asyncio.run(run())
    finally:
        if session.writable:
            lease.release()
        session.close()
