from __future__ import annotations

import asyncio
from contextlib import nullcontext
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from scripts.clickhouse import smoke_strategy_one_app_route as probe
from src.backend import app


def _ready() -> dict:
    return {
        "checks": [], "window": {"sessions": ["2026-08-18"]},
        "initial_cash": 10_000, "execution_interval": "100ms",
        "strategy_run_ready": True, "configuration_revision_id": "release-1",
        "run_plan_id": "plan-1",
    }


def test_app_probe_defaults_to_read_only_preflight(monkeypatch) -> None:
    preflight = Mock(return_value=_ready())
    create = AsyncMock()
    monkeypatch.setattr(app, "_trading_historical_preflight_payload", preflight)
    monkeypatch.setattr(app, "trading_backtest_run_create", create)
    asyncio.run(probe._run(date(2026, 8, 18), "", 10, 10_000, False))
    assert preflight.call_args.args[0].initial_cash == 10_000
    create.assert_not_awaited()


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
