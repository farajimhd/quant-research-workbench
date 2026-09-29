"""The inactive V4 launch path owns four clients and one Keeper session."""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.backend import backtest_fixed_journal_bootstrap as bootstrap
from src.backend import backtest_journal_clickhouse, backtest_fixed_v4_certification
from src.backend import backtest_v4_keeper_lease
from src.backend.replay_run_service import ReplayRunController
from src.trading_runtime import (
    arte_backtest_definition, arte_journal_writer, keeper_ownership,
    keeper_session,
)
from src.trading_runtime.runtime import RunMode
from src.trading_runtime.arte_typed_insert_dispatch import TypedInsertDispatch


def test_v4_writer_consumes_only_a_fresh_same_client_preflight(monkeypatch):
    audited = []
    monkeypatch.setattr(arte_journal_writer, "storage_preflight",
                        lambda client, **_kwargs: audited.append(("storage", client)))
    monkeypatch.setattr(arte_journal_writer, "journal_permission_preflight",
                        lambda client, **_kwargs: audited.append(("grants", client)))
    monkeypatch.setattr(arte_journal_writer, "_verify_run_identity",
                        lambda _client, _run_id: {
                            "mode": "backtest", "account_ids": ("SIM-01",),
                        })
    client = SimpleNamespace(
        typed_insert_strict=True, typed_insert_dispatch=TypedInsertDispatch(object()),
        execute=lambda sql: ('{"name":"strategy_one_entry_context_v1"}'
                             if "FROM system.tables" in sql
                             and "strategy_one_entry_context_v1" in sql else ""),
        close=lambda: None,
    )
    seal = arte_journal_writer._v4_preflight(client)
    assert [kind for kind, _ in audited] == ["storage", "grants"]
    writer = arte_journal_writer.ArteJournalWriter(
        client, run_id=str(uuid4()), journal_profile="backtest_v4",
        coalesce_batches=False, v4_preflight_seal=seal)
    writer.close()
    assert len(audited) == 2
    with pytest.raises(RuntimeError, match="fresh same-client"):
        arte_journal_writer.ArteJournalWriter(
            client, run_id=str(uuid4()), journal_profile="backtest_v4",
            coalesce_batches=False, v4_preflight_seal=seal)
    other = SimpleNamespace(
        typed_insert_strict=True, typed_insert_dispatch=TypedInsertDispatch(object()),
    )
    fresh = arte_journal_writer._v4_preflight(client)
    with pytest.raises(RuntimeError, match="fresh same-client"):
        arte_journal_writer.ArteJournalWriter(
            other, run_id=str(uuid4()), journal_profile="backtest_v4",
            coalesce_batches=False, v4_preflight_seal=fresh)


def test_resumed_v4_controller_requires_exact_later_epoch_lane(monkeypatch):
    from dataclasses import replace
    from src.backend.backtest_fixed_running_anchor import FixedRunningPrefixAnchor
    from src.backend.backtest_journal_memory import BacktestMemoryJournal
    from src.backend.backtest_fixed_journal_bootstrap import (
        FixedJournalAssembly, FixedV4JournalPreflightToken,
    )

    run_id = str(uuid4())
    batch_id = str(uuid4())
    anchor = FixedRunningPrefixAnchor(
        run_id, batch_id, 7, "2026-08-18:100", "2026-08-18",
        100, 1, datetime(2026, 8, 18, 8, 0, 0, 100_000,
                        tzinfo=timezone.utc), None)
    class Keeper:
        pass
    keeper = Keeper()
    class Lease:
        def __init__(self, epoch):
            self.run_id = run_id
            self.epoch = epoch
            self.owner = SimpleNamespace(_session=keeper)
            self.checked = 0
        def assert_current(self):
            self.checked += 1
    monkeypatch.setattr(backtest_v4_keeper_lease, "BacktestV4KeeperLease", Lease)
    monkeypatch.setattr(keeper_session, "ManagedKeeperSession", Keeper)
    lease = Lease(2)
    journal = BacktestMemoryJournal(run_id=run_id, initial_sequence=7)
    publisher = SimpleNamespace(_sequence=7, _batch_id=batch_id,
                                _source_cursor=anchor.source_cursor)
    writer = SimpleNamespace(_client=SimpleNamespace(backtest_v4_lease=lease))
    token = FixedV4JournalPreflightToken(
        run_id, ("SIM-01",), date(2026, 8, 1), "a" * 64, "b" * 64, "c" * 64)
    assembly = FixedJournalAssembly(token, journal, writer, publisher, None)
    controller = object.__new__(ReplayRunController)
    controller.run_id = run_id
    controller._fixed_v4_runtime_image = SimpleNamespace(anchor=anchor)
    controller._journal = None
    controller._fixed_v4_account_ids = None
    controller._resumed_v4_admitted = False
    def attach(value):
        assert value is assembly
        controller._journal = journal
        controller._journal_publisher = publisher
    controller._attach_fixed_journal_assembly = attach
    with pytest.raises(RuntimeError, match="differs from its cold actor cursor"):
        controller._attach_resumed_fixed_v4_assembly(
            assembly, keeper=keeper, lease=Lease(1), anchor=anchor)
    with pytest.raises(RuntimeError, match="differs from its cold actor cursor"):
        controller._attach_resumed_fixed_v4_assembly(
            assembly, keeper=keeper, lease=lease,
            anchor=replace(anchor, boundary_ms=200))
    assert controller._journal is None
    controller._attach_resumed_fixed_v4_assembly(
        assembly, keeper=keeper, lease=lease, anchor=anchor)
    assert controller._resumed_v4_admitted
    assert controller._fixed_keeper_session is keeper
    assert controller._fixed_v4_lease is lease
    assert controller._fixed_v4_account_ids == ("SIM-01",)
    assert lease.checked == 1
    from src.backend import replay_run_service
    controller.definition = SimpleNamespace(
        archived_review_only=False, mode=RunMode.BACKTEST,
        execution_interval="100ms",
        configuration_revision={"payload": {"strategy": {
            "strategy_number": 1, "revision": 1,
            "execution_interval": "100ms"}}})
    controller._task = None
    monkeypatch.setattr(replay_run_service, "_backtest_launch_blocker", lambda _d: "")
    async def no_op():
        pass
    controller._run = no_op
    async def start():
        await controller.start()
        await controller._task
    asyncio.run(start())
    assert lease.checked == 2


def test_v4_handoff_pins_accounts_and_closes_control_clients(monkeypatch):
    calls = []
    account = {"account_key": "main", "modes": ["backtest"]}
    definition = SimpleNamespace(
        mode=RunMode.BACKTEST, execution_interval="100ms",
        session_date=date(2026, 8, 18),
        session_start=datetime(2026, 8, 18, 8, tzinfo=timezone.utc),
        configuration_revision={"content_hash": "a" * 64, "payload": {
            "strategy": {"strategy_number": 1,
                         "strategy_id": "early-squeeze-strategy", "revision": 1},
            "accounts": {"bindings": [account]},
            "run_plan": {"run_plan_id": "strategy-one"},
        }}, market_data_plan={"token": "b" * 64})
    controller = object.__new__(ReplayRunController)
    controller.definition = definition
    controller.run_id = str(uuid4())
    controller.created_at = datetime(2026, 9, 26, tzinfo=timezone.utc)
    controller._journal = None
    controller._resume_state = None
    controller._fixed_keeper_session = None
    controller._fixed_v4_account_ids = None

    async def plans():
        calls.append("sealed_plans")
        return SimpleNamespace(
            market=SimpleNamespace(token="b" * 64),
            execution_market=SimpleNamespace(token="c" * 64))

    controller._fixed_strategy_one_plans = plans
    monkeypatch.setattr(backtest_journal_clickhouse, "backtest_code_hash",
                        lambda _root: "d" * 64)
    monkeypatch.setattr(backtest_fixed_v4_certification,
                        "certify_strategy_one_v4_projection",
                        lambda: "e" * 64)

    class Resource:
        def __init__(self, name):
            self.name = name

        def close(self):
            calls.append(f"close:{self.name}")

    session = Resource("keeper")
    session.client = object()
    monkeypatch.setattr(keeper_session, "open_workstation_keeper_session",
                        lambda: session)
    class Lease:
        def attest_genesis(self, **kwargs):
            assert kwargs == {
                "configuration_hash": "a" * 64,
                "market_plan_token": "b" * 64,
                "code_hash": "d" * 64,
            }
            calls.append("attest_genesis")

        def release(self):
            calls.append("release_run_lease")

    lease = Lease()
    monkeypatch.setattr(backtest_v4_keeper_lease.BacktestV4KeeperLease,
                        "acquire", lambda found, **_kwargs: lease)
    monkeypatch.setattr(arte_journal_writer, "backtest_v4_context_client_from_env",
                        lambda *, keeper_session: Resource("context"))
    monkeypatch.setattr(arte_journal_writer, "backtest_v4_operator_client_from_env",
                        lambda: Resource("reader"))
    monkeypatch.setattr(arte_journal_writer, "backtest_v4_journal_client_from_env",
                        lambda *, keeper_session, lease: Resource("writer_client"))
    assembly = SimpleNamespace(writer=Resource("writer"), journal=Resource("journal"))

    def publish(context, reader, writer, terminal, **kwargs):
        calls.append("publish")
        assert len({id(context), id(reader), id(writer), id(terminal)}) == 4
        assert kwargs["account_ids"] == ("SIM-01-MAIN",)
        assert kwargs["run"]["run_month"] == "2026-09-01"
        assert kwargs["run"]["session_date"] == "2026-08-18"
        assert kwargs["config"]["strategy_revision"] == 1
        assert kwargs["expected_config"]["strategy_id"] == "early-squeeze-strategy"
        assert kwargs["expected_config"]["strategy_revision"] == 1
        assert kwargs["batch_size"] == 4096
        assert kwargs["projection_certifier"]() == "e" * 64
        return assembly

    monkeypatch.setattr(bootstrap, "publish_and_assemble_fixed_v4_journal", publish)
    class Coordinator:
        def __init__(self, client):
            assert client is session.client

        def acquire_portfolio_admission_lease(self, resource_id, *, owner_id,
                                               ttl_seconds):
            assert resource_id == f"backtest-definition:{controller.run_id}"
            assert ttl_seconds == 300.0
            return {"owner_id": owner_id, "epoch": 1}

        def release_portfolio_admission_lease(self, resource_id, *, owner_id,
                                               epoch):
            calls.append("release_definition_claim")
            return True

    monkeypatch.setattr(keeper_ownership, "KeeperOwnershipCoordinator", Coordinator)
    monkeypatch.setattr(arte_backtest_definition, "publish_backtest_definition",
                        lambda writer, run_id, found, **kwargs: calls.append(
                            "publish_definition"))
    def attach(found):
        assert found is assembly
        controller._journal_writer = assembly.writer
        controller._journal = assembly.journal
        controller._journal_publisher = None
        calls.append("attach")

    controller._attach_fixed_journal_assembly = attach
    asyncio.run(controller._open_fixed_journal())
    assert controller._fixed_keeper_session is session
    assert controller._fixed_v4_account_ids == ("SIM-01-MAIN",)
    assert calls[:2] == ["sealed_plans", "publish"]
    assert "publish_definition" in calls
    assert "release_definition_claim" in calls
    assert calls[-1] == "attach"
    assert calls.count("close:reader") == 2
    assert "close:context" in calls and "close:keeper" not in calls
    asyncio.run(controller._close_fixed_journal())
    assert calls[-1] == "close:keeper"
    assert "release_run_lease" in calls
    assert controller._stage_timings["strategy_one_journal_writer_close"]["calls"] == 1
    assert controller._stage_timings["strategy_one_keeper_close"]["calls"] == 1
