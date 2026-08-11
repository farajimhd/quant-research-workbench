from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

from src.backend.app import trading_backtest_run_canvas


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


if __name__ == "__main__":
    unittest.main()
