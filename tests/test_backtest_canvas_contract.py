from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

from src.backend.app import (
    BacktestDebugRunCreateRequest,
    BacktestRunCreateRequest,
    ReplayRunCommandRequest,
    trading_backtest_debug_run_command,
    trading_backtest_debug_run_canvas,
    trading_backtest_debug_run_create,
    trading_backtest_run_create,
    trading_backtest_run_canvas,
    trading_backtest_run_command,
)
from src.trading_runtime.runtime import RunMode


class BacktestCanvasContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_projects_canvas_from_the_pinned_backtest_controller(self) -> None:
        controller = MagicMock()
        controller.canvas_payload = AsyncMock(
            return_value={
                "preview_kind": "backtest_run",
                "strategy": {"runtime_mode": "backtest"},
            }
        )
        with patch("src.backend.app.backtest_run_service.get", return_value=controller) as get:
            payload = await trading_backtest_run_canvas("run-1", symbol="MSFT")

        get.assert_called_once_with("run-1")
        controller.canvas_payload.assert_awaited_once_with("MSFT")
        self.assertEqual(payload["preview_kind"], "backtest_run")
        self.assertEqual(payload["strategy"]["runtime_mode"], "backtest")

    async def test_missing_run_is_a_typed_not_found(self) -> None:
        with patch("src.backend.app.backtest_run_service.get", side_effect=KeyError("missing")):
            with self.assertRaises(HTTPException) as raised:
                await trading_backtest_run_canvas("missing")

        self.assertEqual(raised.exception.status_code, 404)
        self.assertEqual(raised.exception.detail, "Backtest run not found")

    async def test_debug_create_binds_fixture_to_shared_historical_controller(self) -> None:
        controller = MagicMock()
        controller.snapshot.return_value = {
            "mode": "backtest_debug",
            "debug_fixture": {"fixture_id": "gap-open"},
        }
        approved = {"revision_id": "approved-1", "payload": {}}
        request = BacktestDebugRunCreateRequest(
            session_date=date(2026, 7, 28),
            start_time="09:45:00",
            configuration_revision_id="approved-1",
            fixture_id="gap-open",
            tickers=["AAPL"],
            market_events=[{
                "kind": "trade",
                "ticker": "AAPL",
                "ts": "2026-07-28T09:45:00-04:00",
                "price": 101.25,
            }],
            watchlist_events=[{
                "effective_at": "2026-07-28T09:45:00-04:00",
                "event": "added",
                "ibkr_conid": 265598,
                "ticker": "AAPL",
                "watchlist_id": "squeeze-tradable-candidates",
            }],
        )
        with (
            patch(
                "src.backend.app.backtest_debug_configuration_snapshot",
                return_value=approved,
            ) as configuration_snapshot,
            patch(
                "src.backend.app.backtest_debug_run_service.create",
                new=AsyncMock(return_value=controller),
            ) as create,
        ):
            payload = await trading_backtest_debug_run_create(request)

        definition = create.await_args.args[0]
        configuration_snapshot.assert_called_once_with("", candidate_id="approved-1")
        self.assertEqual(definition.mode, RunMode.BACKTEST_DEBUG)
        self.assertEqual(definition.debug_fixture.fixture_id, "gap-open")
        self.assertEqual(definition.debug_fixture.watchlist_events[0]["ticker"], "AAPL")
        self.assertEqual(payload["mode"], "backtest_debug")

    async def test_backtest_create_uses_backtest_configuration_authority(self) -> None:
        controller = MagicMock()
        controller.snapshot.return_value = {"mode": "backtest"}
        approved = {"revision_id": "approved-backtest", "payload": {}}
        request = BacktestRunCreateRequest(
            anchor_date=date(2026, 7, 28),
            session_count=1,
            configuration_revision_id="approved-backtest",
        )
        with (
            patch(
                "src.backend.app.backtest_configuration_snapshot",
                return_value=approved,
            ) as configuration_snapshot,
            patch(
                "src.backend.app.backtest_preflight",
                return_value={
                    "strategy_run_ready": True,
                    "window": {"sessions": ["2026-07-28"]},
                },
            ),
            patch(
                "src.backend.app.backtest_run_service.create",
                new=AsyncMock(return_value=controller),
            ) as create,
        ):
            payload = await trading_backtest_run_create(request)

        configuration_snapshot.assert_called_once_with(
            "", candidate_id="approved-backtest"
        )
        definition = create.await_args.args[0]
        self.assertEqual(definition.mode, RunMode.BACKTEST)
        self.assertIs(definition.configuration_revision, approved)
        self.assertEqual(payload["mode"], "backtest")

    async def test_debug_canvas_uses_debug_service_and_preserves_runtime_mode(self) -> None:
        controller = MagicMock()
        controller.canvas_payload = AsyncMock(
            return_value={
                "preview_kind": "backtest_debug_run",
                "strategy": {"runtime_mode": "backtest_debug"},
            }
        )
        with patch(
            "src.backend.app.backtest_debug_run_service.get", return_value=controller
        ) as get:
            payload = await trading_backtest_debug_run_canvas("run-1", symbol="MSFT")

        get.assert_called_once_with("run-1")
        controller.canvas_payload.assert_awaited_once_with("MSFT")
        self.assertEqual(payload["strategy"]["runtime_mode"], "backtest_debug")

    async def test_backtest_and_debug_share_bounded_lifecycle_commands(self) -> None:
        backtest = MagicMock()
        backtest.command = AsyncMock(return_value={"mode": "backtest", "status": "paused"})
        debug = MagicMock()
        debug.command = AsyncMock(return_value={"mode": "backtest_debug", "status": "running"})
        with (
            patch("src.backend.app.backtest_run_service.get", return_value=backtest),
            patch("src.backend.app.backtest_debug_run_service.get", return_value=debug),
        ):
            paused = await trading_backtest_run_command(
                "run-1", ReplayRunCommandRequest(command="pause")
            )
            resumed = await trading_backtest_debug_run_command(
                "run-2", ReplayRunCommandRequest(command="play")
            )

        backtest.command.assert_awaited_once_with("pause")
        debug.command.assert_awaited_once_with("play")
        self.assertEqual(paused["status"], "paused")
        self.assertEqual(resumed["status"], "running")

    async def test_automatic_historical_modes_reject_replay_only_commands(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            await trading_backtest_run_command(
                "run-1", ReplayRunCommandRequest(command="step", step_seconds=1)
            )

        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("pause, play, or stop", raised.exception.detail)


if __name__ == "__main__":
    unittest.main()
