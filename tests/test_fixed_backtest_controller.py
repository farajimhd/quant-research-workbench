"""Controller-level fixed-boundary ordering without an event replay source."""
import asyncio
from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
import json
from zoneinfo import ZoneInfo

import pytest
import src.backend.backtest_market_data as market_data
from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.backend.backtest_market_data import (
    CertifiedMarketDayPlan, CompletedBoundaryValidator, ExecutionInterval,
)
from src.backend.replay_run_service import ReplayRunController, ReplayRunService, RunMode, _fixed_market_evidence_gaps, _persisted_market_day_frame
from src.trading_runtime.ibkr_schema import OrderRequest
from src.trading_runtime.runtime import RunConfig, TradingRuntime
from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter, SimulationConfig
from src.trading_runtime.journal_contract import JournalRecord
from tests.test_trading_runtime import quote


NY = ZoneInfo("America/New_York")
DAY = "2026-08-18"
RUN = "00000000-0000-0000-0000-000000000001"


def test_backtest_start_rejects_before_legacy_journal_or_disk_write(tmp_path, monkeypatch):
    from src.backend import backtest_journal_clickhouse, replay_run_service
    from src.backend.backtest_market_data import FIXED_EXECUTION_BLOCKER

    controller = object.__new__(ReplayRunController)
    controller.definition = SimpleNamespace(
        archived_review_only=False, mode=RunMode.BACKTEST,
        execution_interval="100ms",
    )
    controller._task = None
    controller.run_dir = tmp_path / "must-not-exist"
    monkeypatch.setattr(backtest_journal_clickhouse, "publish_run", lambda *_a, **_k:
                        (_ for _ in ()).throw(AssertionError("retired bt_* write")))
    monkeypatch.setattr(replay_run_service, "TradingJournal", lambda *_a, **_k:
                        (_ for _ in ()).throw(AssertionError("SQLite opened")))
    with pytest.raises(RuntimeError, match="Fixed-interval Backtest remains blocked") as exc:
        asyncio.run(controller.start())
    assert str(exc.value) == FIXED_EXECUTION_BLOCKER
    assert not controller.run_dir.exists()


def test_zero_source_native_candidates_fail_before_full_universe_read(monkeypatch):
    from src.backend import replay_run_service

    controller = object.__new__(ReplayRunController)
    controller.definition = SimpleNamespace(configuration_revision={"payload": {
        "assignments": [],
        "signal_activation": {"signal_streams": [{
            "enabled": True, "signal_stream_id": "price-squeeze-early"}]},
        "run_plan": {"signal_stream_ids": ["price-squeeze-early"],
                     "activation": {"watchlist_policy": "not_required"}},
    }})
    controller._historical_external_signal_events = []
    controller._historical_core_signal_plans = ()
    controller._fixed_certified_market_plan = AsyncMock(return_value=object())
    monkeypatch.setattr(replay_run_service, "_fixed_market_evidence_gaps", lambda _: ())
    monkeypatch.setattr(market_data, "iter_market_day_rows", lambda *_a, **_k:
                        pytest.fail("full-universe market read"))
    with pytest.raises(RuntimeError, match="zero-candidate terminal authority is not typed"):
        asyncio.run(controller._run_fixed_market_days())


@pytest.mark.parametrize("interval,blocker_name", [
    ("100ms", "FIXED_EXECUTION_BLOCKER"),
    ("events", "EVENT_EXECUTION_BLOCKER"),
])
@pytest.mark.parametrize("backend", ["arte_typed_journal_v1", "arte_clickhouse_v1", "sqlite_v1"])
def test_backtest_resume_rejects_before_legacy_journal_access(
    monkeypatch, tmp_path, interval, blocker_name, backend,
):
    from src.backend import replay_run_service
    from src.backend import backtest_market_data

    run_dir = tmp_path / RUN
    run_dir.mkdir()
    (run_dir / "manifest.json").write_text(json.dumps({
        "journal_backend": backend, "run": {"status": "stopped"},
    }), encoding="utf-8")
    definition = SimpleNamespace(mode=RunMode.BACKTEST, execution_interval=interval)
    monkeypatch.setattr(replay_run_service, "_definition_from_manifest", lambda *_a, **_k: definition)
    monkeypatch.setattr(backtest_market_data, "readonly_clickhouse_client", lambda:
                        (_ for _ in ()).throw(AssertionError("Retired bt_* journal queried")))
    monkeypatch.setattr(replay_run_service.TradingJournal, "__init__", lambda *_a, **_k:
                        (_ for _ in ()).throw(AssertionError("SQLite opened")))
    monkeypatch.setattr(replay_run_service, "ReplayRunController", lambda *_a, **_k:
                        (_ for _ in ()).throw(AssertionError("Controller constructed")))
    service = ReplayRunService(runtime_root=tmp_path)
    with pytest.raises(RuntimeError) as exc:
        asyncio.run(service.resume(RUN))
    assert str(exc.value) == getattr(backtest_market_data, blocker_name)
    assert not (run_dir / "journal.sqlite3").exists()


def test_future_fixed_manifest_names_typed_journal_without_legacy_identity(
    monkeypatch, tmp_path,
):
    from src.backend import replay_run_service

    controller = object.__new__(ReplayRunController)
    controller.run_dir = tmp_path / RUN
    controller.run_dir.mkdir()
    controller.runtime_root = tmp_path
    controller.definition = SimpleNamespace(
        mode=RunMode.BACKTEST, debug_fixture=None,
        configuration_revision={"revision_id": "r", "content_hash": "a" * 64},
        payload=lambda: {"mode": "backtest"},
    )
    controller.snapshot = lambda **_kwargs: {"run_id": RUN, "mode": "backtest",
                                             "status": "completed"}
    monkeypatch.setattr(replay_run_service, "_replay_run_list_projection",
                        lambda *_args, **_kwargs: {})
    monkeypatch.setattr(replay_run_service, "_run_selection_projection",
                        lambda *_args, **_kwargs: {})
    controller._write_manifest()
    assert not (controller.run_dir / "manifest.json").exists()
    with pytest.raises(RuntimeError, match="cannot persist configuration on disk"):
        controller._write_approved_configuration()
    assert not (controller.run_dir / "approved-configuration.json").exists()


def test_typed_saved_review_fails_before_retired_or_sqlite_reader(monkeypatch, tmp_path):
    from src.backend import backtest_review, replay_run_service

    run_dir = tmp_path / RUN
    run_dir.mkdir()
    (run_dir / "manifest.json").write_text(json.dumps({
        "journal_backend": "arte_typed_journal_v1",
    }), encoding="utf-8")
    (run_dir / "journal.sqlite3").write_text("must-not-open", encoding="utf-8")
    monkeypatch.setattr(backtest_review, "ClickHouseSavedBacktestReview",
                        lambda *_args: (_ for _ in ()).throw(AssertionError("bt_* opened")))
    monkeypatch.setattr(backtest_review, "SavedBacktestReview",
                        lambda *_args: (_ for _ in ()).throw(AssertionError("SQLite opened")))
    service = ReplayRunService(runtime_root=tmp_path)
    with pytest.raises(ValueError, match="Typed Backtest saved review is not available"):
        asyncio.run(service._review_saved(RUN))


def test_legacy_clickhouse_saved_review_still_dispatches(monkeypatch, tmp_path):
    from src.backend import backtest_review

    run_dir = tmp_path / RUN
    run_dir.mkdir()
    (run_dir / "manifest.json").write_text(json.dumps({
        "journal_backend": "arte_clickhouse_v1",
    }), encoding="utf-8")
    sentinel = SimpleNamespace(review_only=True)
    monkeypatch.setattr(backtest_review, "ClickHouseSavedBacktestReview",
                        lambda actual: sentinel if actual == run_dir else None)
    service = ReplayRunService(runtime_root=tmp_path)
    service._admit = AsyncMock()
    assert asyncio.run(service._review_saved(RUN)) is sentinel
    service._admit.assert_awaited_once_with(sentinel)


def test_fixed_market_rejects_event_only_strategy_evidence():
    assert _fixed_market_evidence_gaps({"assignments": [{"parameters": {
        "market_pressure": {"enabled": True},
        "historical_hod": {"setup_quote_confirmation_enabled": 1,
                           "setup_minimum_volume_ratio": 2,
                           "setup_minimum_session_relative_volume": 1.5},
    }}]}) == ("market_pressure", "quote_geometry", "session_relative_volume", "trade_volume")
    assert _fixed_market_evidence_gaps({"assignments": []}) == ()


def test_fixed_market_rejects_unpersisted_signal_sources():
    configuration = {
        "assignments": [],
        "run_plan": {"signal_stream_ids": ["price-squeeze-early", "news-alpha"]},
        "signal_activation": {"signal_streams": [
            {"enabled": True, "signal_stream_id": "price-squeeze-early",
             "occurrence_source": "qmd_squeeze_episode"},
            {"enabled": True, "signal_stream_id": "news-alpha",
             "source_type": "news_events"},
        ]},
    }
    assert _fixed_market_evidence_gaps(configuration) == ("signal_stream:news-alpha",)


def test_fixed_runtime_never_materializes_qmd_or_news_signals(monkeypatch):
    from src.backend import historical_watchlist_feature_service

    controller = object.__new__(ReplayRunController)
    controller.definition = SimpleNamespace(
        mode=RunMode.BACKTEST, execution_interval="100ms", debug_fixture=None,
        configuration_revision={"payload": {
            "run_plan": {"signal_stream_ids": ["news-alpha"]},
            "signal_activation": {"signal_streams": [{
                "signal_stream_id": "news-alpha", "source_type": "news_events",
                "enabled": True}]}}},
    )
    controller._journal = object()
    controller._historical_core_signal_plans = ({"signal_stream_id": "legacy"},)
    monkeypatch.setattr(historical_watchlist_feature_service,
                        "materialize_historical_watchlist_plans",
                        lambda *_args, **_kwargs: pytest.fail("QMD materialization"))
    with pytest.raises(RuntimeError, match="cannot materialize QMD"):
        asyncio.run(controller._load_market_signal_events())
    with pytest.raises(RuntimeError, match="certified arte news"):
        asyncio.run(controller._load_external_signal_events())


def test_fixed_boundary_never_reads_or_journals_historical_watchlist():
    controller = object.__new__(ReplayRunController)
    controller.definition = SimpleNamespace(
        mode=RunMode.BACKTEST, execution_interval="100ms")
    controller._historical_watchlist_plans = []
    controller._historical_watchlist_timeline_cache = None
    controller._historical_watchlist_timeline = lambda: pytest.fail("QMD Watchlist")
    controller._journal = SimpleNamespace(
        append=lambda **_kwargs: pytest.fail("Watchlist journal emission"))
    controller._apply_historical_watchlist_membership(
        datetime(2026, 8, 18, 4, tzinfo=NY))
    controller._historical_watchlist_timeline_cache = [{"effective_at":
        datetime(2026, 8, 18, 4, tzinfo=NY)}]
    with pytest.raises(RuntimeError, match="cannot consume a QMD historical Watchlist"):
        controller._apply_historical_watchlist_membership(
            datetime(2026, 8, 18, 4, tzinfo=NY))


def test_fixed_engine_opens_clickhouse_journal_before_any_sqlite(monkeypatch):
    from src.backend import replay_run_service

    controller = object.__new__(ReplayRunController)
    controller.definition = SimpleNamespace(mode=RunMode.BACKTEST,
                                            execution_interval="100ms")
    controller.status = "created"
    controller._journal_writer = None
    controller._prepared_v7 = None
    controller._session_relative_volume_store = SimpleNamespace(close=lambda: None)
    controller._publish = AsyncMock()
    controller._finish = AsyncMock()
    controller._open_fixed_journal = AsyncMock(side_effect=RuntimeError("journal unavailable"))
    monkeypatch.setattr(replay_run_service, "TradingJournal", lambda *_args, **_kwargs:
                        (_ for _ in ()).throw(AssertionError("SQLite opened")))

    asyncio.run(controller._run_engine())

    controller._open_fixed_journal.assert_awaited_once()
    controller._finish.assert_awaited_once_with("failed")
    assert "journal unavailable" in controller.error


def test_inactive_fixed_terminal_handoff_orders_cursor_finish_capture_worker_audit(monkeypatch):
    from src.backend import backtest_terminal_v2_publication as publication
    from src.backend import backtest_terminal_v2_accounts as recovery
    from src.backend import backtest_terminal_v2_keeper as keeper_module
    from src.backend import replay_run_service
    from src.backend.backtest_terminal_v2_keeper import FixedTerminalKeeperAuthority
    from tests.test_backtest_terminal_v2_keeper import FakeKeeper
    from tests.test_backtest_terminal_v2_fence import RUN as V2_RUN, _suffix
    from src.trading_runtime import arte_journal_writer

    prefix, *_ = _suffix()
    events = []
    controller = object.__new__(ReplayRunController)
    controller.run_id = V2_RUN
    controller.definition = SimpleNamespace(mode=RunMode.BACKTEST)
    controller._account_map = {"primary": "DU1"}
    controller._journal = BacktestMemoryJournal(run_id=V2_RUN, initial_sequence=1)
    controller._journal_publisher = SimpleNamespace()
    controller._runtime_finished = False
    controller.current_time = datetime(2026, 8, 18, 14, 0, tzinfo=NY)
    controller._source_cursor = {"session_date": DAY, "boundary_ms": 36000000}
    def capture(account_id, *, state_revision, snapshot_at):
        events.append("capture")
        assert account_id == "DU1" and state_revision == 3
        return SimpleNamespace(account_id=account_id)
    async def finish(*, status):
        events.append("runtime_finish")
        controller._journal.append(
            run_id=V2_RUN, category="lifecycle", entity_type="run", entity_id=V2_RUN,
            payload={"status": status}, event_time=controller.current_time)
    controller._runtime = SimpleNamespace(
        finish=finish, portfolio=SimpleNamespace(capture_recovery_snapshot=capture))
    async def checkpoint(at):
        raise AssertionError("fixed terminal handoff cannot write a disk checkpoint")
    controller._save_restart_checkpoint_responsive = checkpoint
    seal = {"last_sequence": 3, "run_id": V2_RUN,
            "batch_id": "00000000-0000-0000-0000-000000000b04"}
    handoff = SimpleNamespace(commit=seal, publication_fields=lambda: {"account_ids": ("DU1",)})
    controller._prepare_terminal_v2_handoff = lambda got, **_: handoff if got == prefix else None
    def verified_v2_prefix(*_args, **kwargs):
        assert kwargs == {"journal_profile": "backtest_v2"}
        return prefix
    monkeypatch.setattr(arte_journal_writer, "load_committed_prefix", verified_v2_prefix)
    monkeypatch.setattr(publication, "publish_terminal_v2_suffix",
                        lambda *_, **__: events.append("publish") or seal)
    monkeypatch.setattr(recovery, "load_terminal_v2_portfolio_accounts",
                        lambda *_, **__: events.append("cold_audit") or {"DU1": {"state_hash": "a" * 64}})
    monkeypatch.setattr(keeper_module, "load_attested_terminal_v2_accounts",
                        lambda *_, **__: events.append("attested_audit") or {"DU1": {"state_hash": "a" * 64}})
    async def run():
        keeper = FakeKeeper()
        keeper.attest_backtest_terminal_v2 = lambda *_, **__: events.append("attest") or b"proof"
        authority = FixedTerminalKeeperAuthority(
            keeper=keeper, client=object(), run_id=V2_RUN,
            account_ids=("DU1",))
        async with authority:
            return await controller._finish_fixed_typed(
                "completed", authority=authority)
    result = asyncio.run(run())
    assert result == {"seal": seal, "accounts": {"DU1": {"state_hash": "a" * 64}}}
    assert events.index("runtime_finish") < events.index("capture")
    assert events.index("capture") < events.index("publish") < events.index("cold_audit")
    assert events.index("cold_audit") < events.index("attest") < events.index("attested_audit")
    assert controller._runtime_finished


def test_fixed_journal_refuses_retired_bt_resume_even_after_typed_preflight(monkeypatch):
    from src.backend import backtest_journal_clickhouse
    from src.backend import backtest_terminal_v2_preflight
    from src.trading_runtime import arte_journal_writer

    class Client:
        closed = False
        def close(self):
            self.closed = True

    client = Client()
    checked = []
    monkeypatch.setattr(arte_journal_writer, "journal_client_from_env", lambda: client)
    monkeypatch.setattr(backtest_terminal_v2_preflight, "terminal_v2_operator_preflight",
                        lambda value: checked.append(("v2_storage_and_grants", value)))
    monkeypatch.setattr(backtest_journal_clickhouse, "load_fenced_checkpoint",
                        lambda *_a: (_ for _ in ()).throw(AssertionError("retired bt_* read")))
    controller = object.__new__(ReplayRunController)
    controller.run_id = RUN
    controller.definition = SimpleNamespace(mode=RunMode.BACKTEST)
    controller._journal = None
    controller._journal_writer = None
    controller._journal_publisher = None

    with pytest.raises(RuntimeError, match="typed journal publication and cold recovery"):
        asyncio.run(controller._open_fixed_journal())
    assert checked == [("v2_storage_and_grants", client)]
    assert client.closed


def test_fixed_journal_attaches_only_pinned_memory_v2_assembly_without_disk_or_sqlite(
    monkeypatch, tmp_path,
):
    from src.backend import backtest_fixed_journal_bootstrap as bootstrap
    from src.trading_runtime import arte_journal_writer

    monkeypatch.setattr(arte_journal_writer, "journal_client_from_env", lambda:
                        pytest.fail("unexpected ClickHouse client"))
    controller = object.__new__(ReplayRunController)
    controller.run_id = RUN
    controller.run_dir = tmp_path / "must-not-exist"
    controller.definition = SimpleNamespace(
        mode=RunMode.BACKTEST,
        configuration_revision={"content_hash": "c" * 64},
        market_data_plan={"token": "market-token"},
    )
    controller._journal = None
    controller._journal_writer = None
    controller._journal_publisher = None
    journal = BacktestMemoryJournal(run_id=RUN)
    writer = SimpleNamespace(run_id=RUN, journal_profile="backtest_v2")
    publisher = SimpleNamespace(journal=journal, writer=writer)
    authority = SimpleNamespace(run_id=RUN, account_ids=("DU1",))
    token = bootstrap.FixedJournalPreflightToken(
        RUN, ("DU1",), date(2026, 8, 1), "c" * 64,
        "market-token", "a" * 64,
    )
    assembly = bootstrap.FixedJournalAssembly(
        token, journal, writer, publisher, authority)
    controller._attach_fixed_journal_assembly(assembly)
    assert controller._journal is journal
    assert controller._journal_writer is writer
    assert controller._journal_publisher is publisher
    assert controller._fixed_terminal_authority is authority
    assert not controller.run_dir.exists()


def test_fixed_controller_prepares_distinct_typed_clients_without_opening_gate(
    monkeypatch, tmp_path,
):
    from src.backend import backtest_fixed_journal_bootstrap as bootstrap
    from tests.test_backtest_fixed_market_authority import _plans

    parent, execution = _plans()
    config = {"mode": "backtest", "assignments": []}
    controller = object.__new__(ReplayRunController)
    controller.run_id = RUN
    controller.run_dir = tmp_path / "must-not-exist"
    controller.definition = SimpleNamespace(
        mode=RunMode.BACKTEST, market_data_plan={"token": parent.token},
        configuration_revision={"content_hash": "c" * 64, "payload": config},
        session_start=datetime(2026, 8, 18, 8, tzinfo=timezone.utc))
    controller._account_map = {"account": "DU1"}
    controller._resume_state = None
    controller._journal = None
    controller._journal_writer = None
    controller._journal_publisher = None
    read, write, terminal, keeper = object(), object(), object(), object()
    calls = []
    monkeypatch.setattr(bootstrap, "storage_preflight",
                        lambda client, *, tables: calls.append(("read_preflight", client)))
    monkeypatch.setattr(bootstrap, "terminal_v2_operator_preflight",
                        lambda client: calls.append(("terminal_preflight", client)))
    monkeypatch.setattr(bootstrap, "terminal_v2_keeper_proof_preflight",
                        lambda value: calls.append(("keeper_preflight", value)))
    context = dict(mode="backtest", account_ids=("DU1",), run_month="2026-08-01",
                   configuration_hash="c" * 64, market_plan_token=parent.token)
    monkeypatch.setattr(bootstrap, "load_typed_run_context",
                        lambda client, run_id: calls.append(("context", client)) or context)
    monkeypatch.setattr(bootstrap, "verify_fixed_run_context",
                        lambda _dispatch, read_client, terminal_client, *, run_id:
                        calls.append(("context_receipt", read_client, terminal_client)) or context)
    class Writer:
        run_id = RUN
        run_mode = "backtest"
        journal_profile = "backtest_v2"
        coalesce_batches = False
        max_events_per_commit = 512
        def close(self):
            calls.append(("close", self))
    def factory(client, **kwargs):
        assert client is write
        calls.append(("writer", client))
        return Writer()
    asyncio.run(controller._prepare_fixed_journal_assembly(
        read_client=read, writer_client=write, terminal_client=terminal,
        keeper=keeper, attempt_id="00000000-0000-0000-0000-000000000a02",
        writer_factory=factory, projection_certifier=lambda: "a" * 64,
        parent_market_plan=parent, execution_market_plan=execution,
        expected_config=config))
    assert controller._journal.run_id == RUN
    assert controller._journal_publisher.writer is controller._journal_writer
    assert [item[0] for item in calls] == [
        "read_preflight", "terminal_preflight", "keeper_preflight",
        "context_receipt", "context", "context", "context", "writer"]
    assert not controller.run_dir.exists()
    controller._journal.close()


def test_fixed_controller_prepares_inactive_v3_with_distinct_clients(monkeypatch, tmp_path):
    from src.backend import backtest_fixed_journal_bootstrap as bootstrap
    from tests.test_backtest_fixed_market_authority import _plans

    parent, execution = _plans()
    config = {"mode": "backtest", "assignments": []}
    controller = object.__new__(ReplayRunController)
    controller.run_id = RUN
    controller.run_dir = tmp_path / "must-not-exist"
    controller.definition = SimpleNamespace(
        mode=RunMode.BACKTEST, market_data_plan={"token": parent.token},
        configuration_revision={"content_hash": "c" * 64, "payload": config},
        session_start=datetime(2026, 8, 18, 8, tzinfo=timezone.utc))
    controller._account_map = {"account": "DU1"}
    controller._resume_state = None
    controller._journal = None
    read, running, terminal, keeper = object(), object(), object(), object()
    context = dict(mode="backtest", account_ids=("DU1",), run_month="2026-08-01",
                   configuration_hash="c" * 64, market_plan_token=parent.token)
    calls = []
    monkeypatch.setattr(bootstrap, "read_v3_preflight",
                        lambda client: calls.append(("read", client)))
    monkeypatch.setattr(bootstrap, "running_v3_preflight",
                        lambda client: calls.append(("running", client)))
    monkeypatch.setattr(bootstrap, "terminal_v3_preflight",
                        lambda client: calls.append(("terminal", client)))
    monkeypatch.setattr(bootstrap, "terminal_v3_keeper_namespace_preflight",
                        lambda value: calls.append(("keeper", value)))
    monkeypatch.setattr(bootstrap, "verify_fixed_run_context",
                        lambda *_a, **_k: context)
    monkeypatch.setattr(bootstrap, "load_typed_run_context",
                        lambda *_a, **_k: context)
    class Writer:
        run_id = RUN
        run_mode = "backtest"
        journal_profile = "backtest_v3"
        coalesce_batches = False
        max_events_per_commit = 512
        def close(self):
            calls.append(("close", self))
    monkeypatch.setattr(bootstrap, "FixedTerminalKeeperAuthority",
                        lambda **kwargs: SimpleNamespace(
                            run_id=kwargs["run_id"], account_ids=kwargs["account_ids"]))
    asyncio.run(controller._prepare_fixed_v3_journal_assembly(
        read_client=read, writer_client=running, terminal_client=terminal,
        keeper=keeper, attempt_id="00000000-0000-0000-0000-000000000a02",
        writer_factory=lambda client, **_k: calls.append(("writer", client)) or Writer(),
        projection_certifier=lambda: "a" * 64,
        query_hash_certifier=lambda: "d" * 64,
        expected_query_sha256="d" * 64,
        parent_market_plan=parent, execution_market_plan=execution,
        expected_config=config))
    assert controller._journal_writer.journal_profile == "backtest_v3"
    assert controller._journal_publisher.writer is controller._journal_writer
    assert ("read", read) in calls and ("running", running) in calls
    assert ("terminal", terminal) in calls and ("writer", running) in calls
    assert not controller.run_dir.exists()
    controller._journal.close()


def test_fixed_controller_v3_rejects_shared_clients_before_preflight(monkeypatch):
    from src.backend import backtest_fixed_journal_bootstrap as bootstrap
    from tests.test_backtest_fixed_market_authority import _plans

    parent, execution = _plans()
    controller = object.__new__(ReplayRunController)
    controller.run_id = RUN
    controller.definition = SimpleNamespace(
        mode=RunMode.BACKTEST, market_data_plan={"token": parent.token},
        configuration_revision={"content_hash": "c" * 64, "payload": {}},
        session_start=datetime(2026, 8, 18, 8, tzinfo=timezone.utc))
    controller._resume_state = None
    controller._journal = None
    monkeypatch.setattr(bootstrap, "prepare_fixed_v3_journal_token",
                        lambda *_a, **_k: pytest.fail("preflight reached"))
    shared = object()
    with pytest.raises(RuntimeError, match="distinct clients"):
        asyncio.run(controller._prepare_fixed_v3_journal_assembly(
            read_client=shared, writer_client=shared, terminal_client=object(),
            keeper=object(), attempt_id="00000000-0000-0000-0000-000000000a02",
            writer_factory=lambda *_a, **_k: pytest.fail("writer reached"),
            projection_certifier=lambda: "a" * 64,
            query_hash_certifier=lambda: "d" * 64,
            expected_query_sha256="d" * 64,
            parent_market_plan=parent, execution_market_plan=execution,
            expected_config={}))


def test_fixed_controller_v3_closes_assembly_on_preflight_drift(monkeypatch):
    from src.backend import backtest_fixed_journal_bootstrap as bootstrap
    from tests.test_backtest_fixed_market_authority import _plans

    parent, execution = _plans()
    config = {"assignments": []}
    controller = object.__new__(ReplayRunController)
    controller.run_id = RUN
    controller.definition = SimpleNamespace(
        mode=RunMode.BACKTEST, market_data_plan={"token": parent.token},
        configuration_revision={"content_hash": "c" * 64, "payload": config},
        session_start=datetime(2026, 8, 18, 8, tzinfo=timezone.utc))
    controller._account_map = {"account": "DU1"}
    controller._resume_state = None
    controller._journal = None
    closed = []
    assembly = SimpleNamespace(
        writer=SimpleNamespace(close=lambda: closed.append("writer")),
        journal=SimpleNamespace(close=lambda: closed.append("journal")))
    monkeypatch.setattr(bootstrap, "prepare_fixed_v3_journal_token",
                        lambda *_a, **_k: object())
    def assemble(*_a, **_k):
        controller.definition.configuration_revision["content_hash"] = "e" * 64
        return assembly
    monkeypatch.setattr(bootstrap, "assemble_fixed_v3_journal", assemble)
    with pytest.raises(RuntimeError, match="changed during preflight"):
        asyncio.run(controller._prepare_fixed_v3_journal_assembly(
            read_client=object(), writer_client=object(), terminal_client=object(),
            keeper=object(), attempt_id="00000000-0000-0000-0000-000000000a02",
            writer_factory=lambda *_a, **_k: None,
            projection_certifier=lambda: "a" * 64,
            query_hash_certifier=lambda: "d" * 64,
            expected_query_sha256="d" * 64,
            parent_market_plan=parent, execution_market_plan=execution,
            expected_config=config))
    assert closed == ["writer", "journal"]
    assert controller._journal is None


def test_fixed_controller_rejects_changed_market_plan_before_typed_client_or_writer(
    monkeypatch,
):
    from src.backend import backtest_fixed_journal_bootstrap as bootstrap
    from tests.test_backtest_fixed_market_authority import _plans

    parent, execution = _plans()
    controller = object.__new__(ReplayRunController)
    controller.run_id = RUN
    controller.definition = SimpleNamespace(
        mode=RunMode.BACKTEST, market_data_plan={"token": "f" * 64},
        configuration_revision={"content_hash": "c" * 64, "payload": {}},
        session_start=datetime(2026, 8, 18, 8, tzinfo=timezone.utc))
    controller._resume_state = None
    controller._journal = None
    monkeypatch.setattr(bootstrap, "prepare_fixed_journal_token",
                        lambda *_a, **_k: pytest.fail("typed client used after plan drift"))
    with pytest.raises(RuntimeError, match="plans or configuration changed"):
        asyncio.run(controller._prepare_fixed_journal_assembly(
            read_client=object(), writer_client=object(), terminal_client=object(),
            keeper=object(), attempt_id="00000000-0000-0000-0000-000000000a02",
            writer_factory=lambda *_a, **_k: pytest.fail("writer started"),
            projection_certifier=lambda: "a" * 64,
            parent_market_plan=parent, execution_market_plan=execution,
            expected_config={}))


def test_fixed_controller_closes_assembly_if_configuration_drifts_during_preflight(
    monkeypatch,
):
    from src.backend import backtest_fixed_journal_bootstrap as bootstrap
    from tests.test_backtest_fixed_market_authority import _plans

    parent, execution = _plans()
    config = {"assignments": []}
    controller = object.__new__(ReplayRunController)
    controller.run_id = RUN
    controller.definition = SimpleNamespace(
        mode=RunMode.BACKTEST, market_data_plan={"token": parent.token},
        configuration_revision={"content_hash": "c" * 64, "payload": config},
        session_start=datetime(2026, 8, 18, 8, tzinfo=timezone.utc))
    controller._account_map = {"account": "DU1"}
    controller._resume_state = None
    controller._journal = None
    closed = []
    fake = SimpleNamespace(
        writer=SimpleNamespace(close=lambda: closed.append("writer")),
        journal=SimpleNamespace(close=lambda: closed.append("journal")))
    monkeypatch.setattr(bootstrap, "prepare_fixed_journal_token", lambda *_a, **_k: object())
    def assemble(*_a, **_k):
        controller.definition.configuration_revision["payload"] = {"assignments": ["changed"]}
        return fake
    monkeypatch.setattr(bootstrap, "assemble_fixed_journal", assemble)
    with pytest.raises(RuntimeError, match="changed during preflight"):
        asyncio.run(controller._prepare_fixed_journal_assembly(
            read_client=object(), writer_client=object(), terminal_client=object(),
            keeper=object(), attempt_id="00000000-0000-0000-0000-000000000a02",
            writer_factory=lambda *_a, **_k: None,
            projection_certifier=lambda: "a" * 64,
            parent_market_plan=parent, execution_market_plan=execution,
            expected_config=config))
    assert closed == ["writer", "journal"]
    assert controller._journal is None


def test_fixed_journal_rejects_changed_assembly_before_attaching(monkeypatch, tmp_path):
    from src.backend import backtest_fixed_journal_bootstrap as bootstrap
    from src.trading_runtime import arte_journal_writer

    monkeypatch.setattr(arte_journal_writer, "journal_client_from_env", lambda:
                        pytest.fail("unexpected ClickHouse client"))
    controller = object.__new__(ReplayRunController)
    controller.run_id = RUN
    controller.run_dir = tmp_path / "must-not-exist"
    controller.definition = SimpleNamespace(
        mode=RunMode.BACKTEST,
        configuration_revision={"content_hash": "c" * 64},
        market_data_plan={"token": "market-token"},
    )
    controller._journal = None
    controller._journal_writer = None
    controller._journal_publisher = None
    journal = BacktestMemoryJournal(run_id=RUN)
    writer = SimpleNamespace(run_id=RUN, journal_profile="backtest_v2")
    publisher = SimpleNamespace(journal=journal, writer=writer)
    authority = SimpleNamespace(run_id=RUN, account_ids=("DU1",))
    token = bootstrap.FixedJournalPreflightToken(
        RUN, ("DU1",), date(2026, 8, 1), "c" * 64,
        "changed-token", "a" * 64,
    )
    assembly = bootstrap.FixedJournalAssembly(
        token, journal, writer, publisher, authority)
    with pytest.raises(RuntimeError, match="differs from pinned run"):
        controller._attach_fixed_journal_assembly(assembly)
    assert controller._journal is None
    assert controller._journal_writer is None
    assert controller._journal_publisher is None
    assert not controller.run_dir.exists()


def test_fixed_v3_assembly_requires_pinned_query_before_inactive_attach(tmp_path):
    from src.backend import backtest_fixed_journal_bootstrap as bootstrap

    controller = object.__new__(ReplayRunController)
    controller.run_id = RUN
    controller.run_dir = tmp_path / "must-not-exist"
    controller.definition = SimpleNamespace(
        mode=RunMode.BACKTEST,
        configuration_revision={"content_hash": "c" * 64},
        market_data_plan={"token": "b" * 64},
    )
    controller._journal = None
    journal = BacktestMemoryJournal(run_id=RUN)
    writer = SimpleNamespace(run_id=RUN, journal_profile="backtest_v3")
    publisher = SimpleNamespace(journal=journal, writer=writer)
    authority = SimpleNamespace(run_id=RUN, account_ids=("DU1",))
    token = bootstrap.FixedV3JournalPreflightToken(
        RUN, ("DU1",), date(2026, 8, 1), "c" * 64,
        "b" * 64, "d" * 64, "a" * 64,
    )
    assembly = bootstrap.FixedJournalAssembly(
        token, journal, writer, publisher, authority)
    with pytest.raises(RuntimeError, match="differs from pinned run"):
        controller._attach_fixed_journal_assembly(assembly)
    with pytest.raises(RuntimeError, match="differs from pinned run"):
        controller._attach_fixed_journal_assembly(
            assembly, expected_query_sha256="e" * 64)
    assert controller._journal is None
    controller._attach_fixed_journal_assembly(
        assembly, expected_query_sha256="d" * 64)
    assert controller._journal is journal
    assert controller._journal_writer is writer
    assert not controller.run_dir.exists()
    journal.close()


def test_fixed_open_remains_blocked_even_with_prepared_assembly(monkeypatch):
    from src.backend import backtest_terminal_v2_preflight
    from src.trading_runtime import arte_journal_writer

    class Client:
        def close(self):
            pass

    monkeypatch.setattr(arte_journal_writer, "journal_client_from_env", Client)
    monkeypatch.setattr(backtest_terminal_v2_preflight,
                        "terminal_v2_operator_preflight", lambda client: None)
    controller = object.__new__(ReplayRunController)
    controller.run_id = RUN
    controller.definition = SimpleNamespace(mode=RunMode.BACKTEST)
    controller._journal = None
    controller._fixed_journal_assembly = object()
    with pytest.raises(RuntimeError, match="typed journal publication and cold recovery"):
        asyncio.run(controller._open_fixed_journal())
    assert controller._journal is None


def test_fixed_journal_shutdown_drains_off_event_loop():
    import threading

    controller = object.__new__(ReplayRunController)
    calls = []
    controller._journal_writer = SimpleNamespace(
        close=lambda: calls.append(("writer", threading.get_ident())))
    controller._journal_publisher = object()
    controller._journal = SimpleNamespace(close=lambda: calls.append(("journal", threading.get_ident())))

    async def exercise():
        loop_thread = threading.get_ident()
        await controller._close_fixed_journal()
        assert calls[0][0] == "writer" and calls[0][1] != loop_thread
        assert calls[1][0] == "journal"

    asyncio.run(exercise())
    assert controller._journal_writer is None
    assert controller._journal_publisher is None


def test_fixed_legacy_activity_route_blocks_without_bt_or_sqlite_fallback(monkeypatch):
    from src.backend import backtest_journal_reader, backtest_market_data

    controller = object.__new__(ReplayRunController)
    controller.run_id = RUN
    controller.definition = SimpleNamespace(
        mode=RunMode.BACKTEST, configuration_revision={"payload": {}})
    controller.current_time = datetime(2026, 8, 18, 4, 1, tzinfo=NY)
    controller._journal = BacktestMemoryJournal(run_id=RUN)
    controller._journal_publisher = SimpleNamespace(
        fenced_sequence=7, committed_batch_ids=(RUN,))
    controller._journal.append(run_id=RUN, category="strategy_decision",
                               entity_type="signal", entity_id="unfenced",
                               event_time=controller.current_time, payload={"action": "enter"})

    class Client:
        def close(self):
            pass

    class Reader:
        def __init__(self, _client, _run_id, **kwargs):
            pytest.fail("retired bt_* reader opened")

    monkeypatch.setattr(backtest_market_data, "readonly_clickhouse_client", Client)
    monkeypatch.setattr(backtest_journal_reader, "BacktestJournalReader", Reader)
    with pytest.raises(RuntimeError, match="normalized V2 UI projection"):
        controller.strategy_activity_snapshot(limit=2)
    with pytest.raises(RuntimeError, match="normalized V2 projection"):
        controller.signal_stream_snapshot(limit=2)


def test_fixed_signal_loader_never_prepares_missing_occurrences(monkeypatch):
    from src.backend import historical_signal_occurrence_service as occurrences

    controller = object.__new__(ReplayRunController)
    controller.definition = SimpleNamespace(
        mode=RunMode.BACKTEST, execution_interval="100ms",
        configuration_revision={"payload": {"run_plan": {
            "signal_stream_ids": ["early"]}, "signal_activation": {
            "signal_streams": [{"signal_stream_id": "early",
                                "occurrence_source": "qmd_squeeze_episode",
                                "enabled": True}]}}},
        requested_start=datetime(2026, 8, 18, 4, tzinfo=NY),
        session_end=datetime(2026, 8, 18, 9, 30, tzinfo=NY),
    )
    controller._journal = object()
    def unavailable(*_args, **_kwargs):
        raise AssertionError("fixed Backtest opened a disk-backed occurrence")
    monkeypatch.setattr(occurrences, "historical_source_native_signal_occurrences", unavailable)
    with pytest.raises(RuntimeError, match="certified arte reader"):
        asyncio.run(controller._load_source_native_signal_events())
    controller.definition.configuration_revision["payload"]["signal_activation"]["signal_streams"][0][
        "historical_occurrence_artifact"] = "unprepared"
    with pytest.raises(RuntimeError, match="certified arte reader"):
        asyncio.run(controller._load_source_native_signal_events())


def test_fixed_journal_fences_at_completed_boundary_before_buffer_fills():
    controller = object.__new__(ReplayRunController)
    controller.run_id = RUN
    controller._journal = BacktestMemoryJournal(run_id=RUN, max_pending_records=2)
    at = datetime(2026, 8, 18, 4, 0, 0, 100000, tzinfo=NY)
    controller._journal.append(run_id=RUN, category="test", entity_type="frame",
                               entity_id="first", event_time=at, payload={})
    controller._source_cursor = {"session_date": DAY, "boundary_ms": 100}
    controller._flush_passive_market_events = lambda: None
    controller._next_action_after_sequence = None
    controller._step_until = None
    controller._fast_forward_until = None
    controller._publish = AsyncMock()
    controller._save_restart_checkpoint_responsive = AsyncMock()
    controller._restart_checkpoint_interval_events = lambda: None
    asyncio.run(controller._after_event(at))
    controller._save_restart_checkpoint_responsive.assert_awaited_once_with(
        at, nonblocking_fixed=True)


def test_fixed_signal_loader_uses_pinned_bars_without_event_fallback(monkeypatch):
    from src.backend import fixed_bar_signal, historical_signal_occurrence_service

    at = datetime(2026, 8, 18, 4, 5, 0, 100000, tzinfo=NY)
    controller = object.__new__(ReplayRunController)
    controller.run_id = RUN
    controller.definition = SimpleNamespace(
        mode=RunMode.BACKTEST, execution_interval="100ms", tickers=(),
        configuration_revision={"payload": {"run_plan": {
            "signal_stream_ids": ["price-squeeze-early"]}, "signal_activation": {
            "signal_streams": [{"signal_stream_id": "price-squeeze-early",
                                "occurrence_source": "qmd_squeeze_episode",
                                "enabled": True}]}}},
        requested_start=at, session_end=at + timedelta(minutes=5),
    )
    controller._journal = BacktestMemoryJournal(run_id=RUN)
    controller._fixed_certified_market_plan = AsyncMock(return_value=object())
    controller._fixed_through_boundary_ms = lambda: 600_000
    authorities = []
    controller._record_data_authority = lambda key, value: authorities.append((key, value))
    class Reader:
        def close(self):
            pass
    monkeypatch.setattr(market_data, "readonly_clickhouse_client", lambda **_kwargs: Reader())
    monkeypatch.setattr(fixed_bar_signal, "load_first_squeeze_occurrences", lambda *_args, **_kwargs: {
        "occurrences": [{"event_id": "bar-signal-1", "signal_stream_id": "price-squeeze-early",
                         "ticker": "AAPL", "available_at": at.isoformat(),
                         "last_price": 10., "squeeze_move_pct": 0.1}],
        "authority": {"authority": fixed_bar_signal.CONTRACT, "row_count": 1},
    })
    monkeypatch.setattr(historical_signal_occurrence_service,
                        "historical_source_native_signal_occurrences",
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(
                            AssertionError("event occurrence fallback used")))
    events = asyncio.run(controller._load_source_native_signal_events())
    assert len(events) == 1 and events[0].ticker == "AAPL"
    assert authorities[0][1]["authority"] == fixed_bar_signal.CONTRACT
    assert controller._journal.latest_sequence(RUN) == 1


def _row(ticker, boundary_ms, resolution_ms):
    return dict(session_date=DAY, ticker=ticker, boundary_ms=boundary_ms,
                resolution_ms=resolution_ms, indicator_resolution_ms=resolution_ms,
                bucket_index=(14_400_000 + boundary_ms) // resolution_ms - 1,
                price_valid=1, quote_valid=1, close_int=100_000,
                execution_vwap=(10.0 if ticker == "AAPL" else 20.0)
                + boundary_ms / 1_000 if resolution_ms == 100 else 0)


@pytest.mark.parametrize("changed", [
    {"ticker": "UNPINNED"},
    {"bucket_index": 0},
    {"session_date": "2026-08-19"},
    {"boundary_ms": 200},
    {"indicator_resolution_ms": 0},
])
def test_completed_market_boundary_rejects_changed_persisted_identity(changed):
    plan = CertifiedMarketDayPlan(
        ExecutionInterval.fixed(100), "build", "definition", (DAY,),
        ("AAPL",), (), (100,), "pinned-token")
    row = {**_row("AAPL", 100, 100), **changed}
    with pytest.raises(ValueError, match="Persisted market"):
        CompletedBoundaryValidator(plan).validate(DAY, 100, [("AAPL", {100: row})])


def test_fixed_runner_rejects_changed_boundary_before_broker_or_cursor(monkeypatch):
    from src.backend import replay_run_service

    plan = CertifiedMarketDayPlan(
        ExecutionInterval.fixed(100), "build", "definition", (DAY,),
        ("AAPL",), (), (100,), "pinned-token")
    bad = {**_row("AAPL", 100, 100), "bucket_index": 0}
    monkeypatch.setattr(market_data, "iter_market_day_rows",
                        lambda *_args, **_kwargs: iter([bad]))
    monkeypatch.setattr(replay_run_service, "_fixed_market_evidence_gaps", lambda _: ())
    controller = object.__new__(ReplayRunController)
    start = datetime(2026, 8, 18, 4, tzinfo=NY)
    controller.definition = SimpleNamespace(
        configuration_revision={"payload": {"assignments": []}},
        requested_start=start, session_end=start + timedelta(seconds=1),
        causal_v7_plan={})
    controller._fixed_certified_market_plan = AsyncMock(return_value=plan)
    controller._fixed_through_boundary_ms = lambda: 1000
    controller._journal = BacktestMemoryJournal(run_id=RUN)
    controller._resume_state = None
    controller._source_cursor = {}
    controller._fixed_vwap_day = None
    controller._fixed_vwap_by_ticker = {}
    controller._historical_external_signal_events = []
    controller._quotes = {}
    controller._stop_requested = False
    controller._record_data_authority = lambda *_args: None
    controller._prepare_session_relative_volume = AsyncMock()
    controller._publish = AsyncMock()
    controller._wait_until_active = AsyncMock()
    controller._runtime = SimpleNamespace(process_liquidity_bar=AsyncMock(
        side_effect=AssertionError("broker saw invalid row")))
    with pytest.raises(ValueError, match="Persisted market row"):
        asyncio.run(controller._run_fixed_market_days())
    controller._runtime.process_liquidity_bar.assert_not_awaited()
    assert controller._source_cursor == {}


def test_fixed_frame_rejects_missing_persisted_indicator_join():
    row = _row("AAPL", 100, 100)
    row["indicator_resolution_ms"] = 0
    with pytest.raises(ValueError, match="matching indicator row"):
        _persisted_market_day_frame(
            row, at=datetime(2026, 8, 18, 4, 0, 0, 100000, tzinfo=NY),
            sequence=1)


def test_fixed_controller_applies_all_liquidity_before_any_strategy_frame(monkeypatch):
    plan = CertifiedMarketDayPlan(
        ExecutionInterval.fixed(100), "build", "definition", (DAY,),
        ("AAPL", "MSFT"), (), (100, 1000), "pinned-token")
    rows = [
        _row("AAPL", 100, 100), _row("MSFT", 100, 100),
        _row("AAPL", 1000, 100), _row("AAPL", 1000, 1000),
        _row("MSFT", 1000, 100), _row("MSFT", 1000, 1000),
    ]
    monkeypatch.setattr(market_data, "certified_market_plan_from_arte",
                        lambda **_kwargs: plan)
    def persisted_rows(_plan, *, through_boundary_ms):
        assert through_boundary_ms == 2_000
        return iter(rows)
    monkeypatch.setattr(market_data, "iter_market_day_rows", persisted_rows)
    controller = object.__new__(ReplayRunController)
    controller.definition = SimpleNamespace(
        configuration_revision={"payload": {"assignments": []}},
        market_data_plan={"token": "pinned-token", "sessions": [DAY]},
        causal_v7_plan={}, tickers=("AAPL", "MSFT"),
        requested_start=datetime.combine(date(2026, 8, 18), time(4), tzinfo=NY),
        session_end=datetime.combine(date(2026, 8, 18), time(4, 0, 2), tzinfo=NY),
    )
    controller._journal = BacktestMemoryJournal(run_id=RUN)
    controller._resume_state = None
    controller._source_cursor = {}
    controller._fixed_vwap_day = None
    controller._fixed_vwap_by_ticker = {}
    controller._historical_external_signal_events = []
    controller._quotes = {}
    controller._stop_requested = False
    controller.processed_events = 0
    controller.warmup_events = 0
    events = []

    class Runtime:
        async def process_liquidity_bar(self, row, *, at):
            events.append(("liquidity", row["boundary_ms"], row["ticker"]))
            return SimpleNamespace(ts=at)

    controller._runtime = Runtime()
    controller._record_data_authority = lambda *_args: None
    controller._apply_historical_watchlist_membership = lambda _at: None
    controller._remember_strategy_frame = lambda frame: events.append(
        ("auxiliary", int((frame.as_of - controller.definition.requested_start).total_seconds() * 1000),
         frame.ticker))

    async def no_op(*_args, **_kwargs):
        return None

    async def strategy(frame):
        boundary_ms = int((frame.as_of - controller.definition.requested_start).total_seconds() * 1000)
        assert set(controller._quotes) == {"AAPL", "MSFT"}
        expected = (10.0 if frame.ticker == "AAPL" else 20.0) + boundary_ms / 1_000
        assert frame.indicator["execution_vwap"] == expected
        events.append(("strategy", boundary_ms, frame.ticker))
        return True

    async def finish(status):
        events.append(("finish", status, ""))

    controller._prepare_session_relative_volume = no_op
    controller._publish = no_op
    controller._wait_until_active = no_op
    controller._after_event = no_op
    controller._process_strategy_frame = strategy
    controller._finish = finish
    asyncio.run(controller._run_fixed_market_days())
    assert events[:4] == [
        ("liquidity", 100, "AAPL"), ("liquidity", 100, "MSFT"),
        ("strategy", 100, "AAPL"), ("strategy", 100, "MSFT"),
    ]
    assert controller.processed_events == 4
    assert events[-1] == ("finish", "completed", "")


def test_fixed_controller_runtime_fills_only_after_decision_boundary(monkeypatch):
    start = datetime(2026, 8, 18, 4, tzinfo=NY)
    plan = CertifiedMarketDayPlan(
        ExecutionInterval.fixed(100), "build", "definition", (DAY,),
        ("AAPL",), (), (100,), "pinned-token")
    rows = []
    for boundary_ms in (100, 200):
        at = start + timedelta(milliseconds=boundary_ms)
        event_us = int(at.timestamp() * 1_000_000) - 1
        rows.append({**_row("AAPL", boundary_ms, 100),
                     "bucket_index": 144000 + boundary_ms // 100 - 1,
                     "first_event_us": event_us, "last_event_us": event_us,
                     "event_count": 1, "quote_timestamp_us": event_us,
                     "bid_int": 99_900, "ask_int": 100_000,
                     "bid_size": 100, "ask_size": 100,
                     "low_int": 99_900, "high_int": 100_000,
                     "extremes_valid": 1, "execution_volume": 0})
    monkeypatch.setattr(market_data, "certified_market_plan_from_arte",
                        lambda **_kwargs: plan)
    monkeypatch.setattr(market_data, "iter_market_day_rows",
                        lambda _plan, **_kwargs: iter(rows))
    controller = object.__new__(ReplayRunController)
    controller.run_id = RUN
    controller.definition = SimpleNamespace(
        configuration_revision={"payload": {"assignments": []}},
        market_data_plan={"token": "pinned-token", "sessions": [DAY]},
        causal_v7_plan={}, tickers=("AAPL",), requested_start=start,
        session_end=start + timedelta(milliseconds=200))
    controller._journal = BacktestMemoryJournal(run_id=RUN)
    controller._resume_state = None
    controller._source_cursor = {}
    controller._fixed_vwap_day = None
    controller._fixed_vwap_by_ticker = {}
    controller._historical_external_signal_events = []
    controller._quotes = {}
    controller._stop_requested = False
    controller.processed_events = 0
    controller.warmup_events = 0
    controller._record_data_authority = lambda *_args: None
    controller._apply_historical_watchlist_membership = lambda _at: None
    controller._remember_strategy_frame = lambda _frame: None

    class NoopStrategy:
        strategy_id = "fixed-integration"
        revision = 1
        automatic = True

    broker = SimulatedBrokerAdapter(
        ["TEST"], SimulationConfig(initial_cash=100_000,
            commission_per_share=0, minimum_commission=0),
        mode=RunMode.BACKTEST, initial_time=start)
    runtime = TradingRuntime(
        RunConfig(RunMode.BACKTEST, "fixed-integration", 1, ("TEST",), start.date(),
                  run_id=RUN, safety_supervisor_enabled=False,
                  write_progress_checkpoints=False),
        broker, NoopStrategy(), controller._journal)
    controller._runtime = runtime
    observed = []

    async def no_op(*_args, **_kwargs):
        return None

    async def decide(frame):
        observed.append((frame.as_of, len([record for record in
            controller._journal.records(RUN)
            if record.category == "execution" and record.entity_type == "fill"])))
        if len(observed) == 1:
            await broker.place_orders("TEST", [OrderRequest(
                acctId="TEST", conid=265598, cOID="fixed-entry", ticker="AAPL",
                orderType="MKT", side="BUY", quantity=5, outsideRTH=True)])

    controller._prepare_session_relative_volume = no_op
    controller._publish = no_op
    controller._wait_until_active = no_op
    controller._after_event = no_op
    controller._process_strategy_frame = decide
    controller._finish = no_op

    async def run():
        await runtime.initialize()
        await controller._run_fixed_market_days()
    asyncio.run(run())
    fills = [record for record in controller._journal.records(RUN)
             if record.category == "execution" and record.entity_type == "fill"]
    assert observed == [(start + timedelta(milliseconds=100), 0),
                        (start + timedelta(milliseconds=200), 1)]
    assert len(fills) == 1
    assert fills[0].event_time == start + timedelta(milliseconds=200)
    assert float(fills[0].payload["price"]) == 10.0

    async def event_reference():
        reference = SimulatedBrokerAdapter(
            ["TEST"], broker.config, mode=RunMode.BACKTEST, initial_time=start)
        await reference.initialize()
        first = start + timedelta(milliseconds=100, microseconds=-1)
        second = start + timedelta(milliseconds=200, microseconds=-1)
        await reference.on_market_event(replace(
            quote(bid=9.99, ask=10.0, ask_size=100), ts=first, ingest_ts=first))
        await reference.place_orders("TEST", [OrderRequest(
            acctId="TEST", conid=265598, cOID="event-entry", ticker="AAPL",
            orderType="MKT", side="BUY", quantity=5, outsideRTH=True)])
        return await reference.on_market_event(replace(
            quote(bid=9.99, ask=10.0, ask_size=100), ts=second, ingest_ts=second))

    event_fills = asyncio.run(event_reference())
    assert [(float(row.payload["price"]), float(row.payload["size"])) for row in fills] == [
        (fill.price, fill.size) for fill in event_fills]


def test_fixed_resume_does_not_redeliver_committed_source_signals(monkeypatch):
    plan = CertifiedMarketDayPlan(
        ExecutionInterval.fixed(100), "build", "definition", (DAY,),
        ("AAPL",), (), (100, 1000), "pinned-token")
    monkeypatch.setattr(market_data, "certified_market_plan_from_arte",
                        lambda **_kwargs: plan)
    monkeypatch.setattr(market_data, "iter_market_day_rows",
                        lambda _plan, **_kwargs: iter([
                            _row("AAPL", 100, 100), _row("AAPL", 200, 100)]))
    start = datetime.combine(date(2026, 8, 18), time(4), tzinfo=NY)
    controller = object.__new__(ReplayRunController)
    controller.definition = SimpleNamespace(
        configuration_revision={"payload": {"assignments": []}},
        market_data_plan={"token": "pinned-token", "sessions": [DAY]},
        causal_v7_plan={}, tickers=("AAPL",), requested_start=start,
        session_end=start + timedelta(seconds=1))
    controller._journal = BacktestMemoryJournal(run_id=RUN)
    controller._resume_state = {}
    controller._source_cursor = {"session_date": DAY, "boundary_ms": 100, "sequence": 1}
    controller._fixed_vwap_day = DAY
    controller._fixed_vwap_by_ticker = {}
    controller._historical_external_signal_events = [
        SimpleNamespace(available_at=start + timedelta(milliseconds=value),
                        occurrence={"ticker": "AAPL"})
        for value in (100, 200)]
    controller._quotes = {}
    controller._stop_requested = False
    controller.processed_events = 0
    controller.warmup_events = 0
    delivered = []

    class Runtime:
        async def process_liquidity_bar(self, _row, *, at):
            return SimpleNamespace(ts=at)

    async def no_op(*_args, **_kwargs):
        return None

    controller._runtime = Runtime()
    controller._record_data_authority = lambda *_args: None
    controller._prepare_session_relative_volume = no_op
    controller._publish = no_op
    controller._wait_until_active = no_op
    controller._after_event = no_op
    controller._finish = no_op
    controller._apply_historical_watchlist_membership = lambda _at: None
    controller._process_strategy_frame = no_op
    controller._process_external_signal_event = lambda event: (
        delivered.append(event.available_at) or no_op())

    asyncio.run(controller._run_fixed_market_days())

    assert delivered == [start + timedelta(milliseconds=200)]
