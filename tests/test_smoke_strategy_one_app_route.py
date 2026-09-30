from __future__ import annotations

import asyncio
import sys
from contextlib import nullcontext
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from scripts.clickhouse import smoke_strategy_one_app_route as probe
from src.backend import app
from src.backend import fixed_v7_stream


def _ready() -> dict:
    return {
        "checks": [], "window": {"sessions": ["2026-08-18"]},
        "initial_cash": 10_000, "execution_interval": "100ms",
        "strategy_run_ready": True, "configuration_revision_id": "release-1",
        "run_plan_id": "plan-1",
    }


def test_public_backtest_resume_stays_closed_without_interrupted_run_parity() -> None:
    assert app.backtest_run_service.allow_typed_backtest_resume is False


def test_app_probe_defaults_to_read_only_preflight(monkeypatch) -> None:
    preflight = Mock(return_value=_ready())
    create = AsyncMock()
    monkeypatch.setattr(app, "_trading_historical_preflight_payload", preflight)
    monkeypatch.setattr(app, "trading_backtest_run_create", create)
    asyncio.run(probe._run(date(2026, 8, 18), "", 10, 10_000, False))
    assert preflight.call_args.args[0].initial_cash == 10_000
    create.assert_not_awaited()


def test_cli_cash_default_matches_app(monkeypatch) -> None:
    run = AsyncMock()
    monkeypatch.setattr(sys, "argv", ["smoke_strategy_one_app_route.py"])
    monkeypatch.setattr(probe, "_load_private_credentials", lambda: None)
    monkeypatch.setattr(probe, "_run", run)
    probe.main()
    assert run.await_args.args[3] == app.BacktestRunCreateRequest.model_fields[
        "initial_cash"].default
    assert run.await_args.args[4] is False


def test_app_probe_runs_one_public_controller_without_files(
    monkeypatch, tmp_path,
) -> None:
    preflight = Mock(return_value=_ready())
    create = AsyncMock(return_value={"run_id": "run-1"})
    async def exercise() -> None:
        completed = asyncio.get_running_loop().create_future()
        completed.set_result(None)
        controller = SimpleNamespace(
            run_id="run-1", status="completed", _task=completed,
            processed_events=7, error="", run_dir=tmp_path / "run-1",
        )
        service = SimpleNamespace(get=Mock(return_value=controller))
        report = Mock()
        monkeypatch.setattr(app, "_trading_historical_preflight_payload", preflight)
        monkeypatch.setattr(app, "trading_backtest_run_create", create)
        monkeypatch.setattr(app, "backtest_run_service", service)
        monkeypatch.setattr(probe, "_profile_sql_calls", lambda _profile: nullcontext())
        monkeypatch.setattr(probe, "_print_completed_profile", report)
        await probe._run(date(2026, 8, 18), "", 10, 10_000, True)
        service.get.assert_called_once_with("run-1")
        report.assert_called_once_with(controller)
    asyncio.run(exercise())
    request = create.await_args.args[0]
    assert request.initial_cash == 10_000
    assert request.experimental_structure_book == "level-book-v7"
    assert request.tickers == []


def test_seed_diagnostic_restores_wrapped_worker_functions(monkeypatch, capsys) -> None:
    seed = Mock(return_value={})
    splits = Mock(return_value={})
    book = Mock(return_value=None)
    monkeypatch.setattr(fixed_v7_stream, "load_seeds_batch", seed)
    monkeypatch.setattr(fixed_v7_stream, "split_evidence_batch", splits)
    monkeypatch.setattr(fixed_v7_stream.FixedV7Stream, "__init__", book)
    with probe._profile_v7_seeds(True):
        assert fixed_v7_stream.load_seeds_batch() == {}
        assert fixed_v7_stream.split_evidence_batch() == {}
        fixed_v7_stream.FixedV7Stream.__new__(
            fixed_v7_stream.FixedV7Stream).__init__()
    assert fixed_v7_stream.load_seeds_batch is seed
    assert fixed_v7_stream.split_evidence_batch is splits
    assert fixed_v7_stream.FixedV7Stream.__init__ is book
    assert "V7 seed seed_select_decode: calls=1" in capsys.readouterr().out


def test_journal_profile_reports_worker_cost_without_payloads(capsys) -> None:
    controller = SimpleNamespace(_journal_writer_final_metrics={
        "committed_units": 2, "committed_event_rows": 31,
        "failed_units": 0, "publish_ns_total": 2_500_000_000,
        "publish_ns_max": 2_000_000_000,
        "publish_by_unit": {
            "V4CompoundBatch": {"units": 1, "publish_ns_total": 2_000_000_000,
                                "publish_ns_max": 2_000_000_000},
            "_TerminalBacktestUnit": {"units": 1, "publish_ns_total": 500_000_000,
                                      "publish_ns_max": 500_000_000},
        },
        "compound_publish_stages_ns": {"stage_detail_insert": 1_000_000_000},
    })
    probe._print_journal_writer_profile(controller)
    output = capsys.readouterr().out
    assert "Journal writer: units=2 rows=31 failed=0 publish_s=2.500" in output
    assert "Journal unit V4CompoundBatch: units=1 publish_s=2.000" in output
    assert "Journal compound stage_detail_insert: worker_s=1.000" in output
    assert "payload" not in output


def test_v7_diagnostic_reports_cache_use_and_restores_methods(monkeypatch, capsys) -> None:
    from src.backend.fixed_v7_stream import FixedV7Cache, FixedV7Stream
    from src.backend.fixed_v7_interval_cache import FixedV7IntervalCache

    stream = Mock(return_value="ready")
    update = Mock()
    monkeypatch.setattr(FixedV7Cache, "_stream", stream)
    monkeypatch.setattr(FixedV7Stream, "update_second", update)
    interval = Mock(return_value=())
    monkeypatch.setattr(FixedV7IntervalCache, "strategy_one_levels", interval)
    with probe._profile_v7_updates(True):
        assert FixedV7Cache._stream(object(), "TEST", as_of="clock") == "ready"
        assert FixedV7IntervalCache.strategy_one_levels(
            object(), "TEST", as_of="clock") == ()
    assert FixedV7Cache._stream is stream
    assert FixedV7Stream.update_second is update
    assert FixedV7IntervalCache.strategy_one_levels is interval
    output = capsys.readouterr().out
    assert "V7 cache stream calls=1 tickers=1 update_threads=0" in output
    assert "V7 interval levels: calls=1 wall_s=" in output
