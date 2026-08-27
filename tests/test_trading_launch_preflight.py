from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import patch

from src.backend.app import (
    HistoricalPreflightRequest,
    ReplayPreflightRequest,
    trading_backtest_debug_preflight,
    trading_historical_preflight,
    trading_replay_preflight,
)


class TradingLaunchPreflightTests(unittest.TestCase):
    @patch("src.backend.app.configuration_candidate", return_value=None)
    @patch("src.backend.app.approved_configuration", return_value=None)
    def test_replay_missing_release_is_a_blocked_readiness_payload(self, _approved, _candidate) -> None:
        payload = trading_replay_preflight(
            ReplayPreflightRequest(session_date=date(2026, 8, 18))
        )

        self.assertFalse(payload["ready"])
        self.assertEqual(payload["checks"][0]["id"], "approved_configuration")
        self.assertEqual(payload["checks"][0]["status"], "blocked")
        self.assertEqual(payload["checks"][0]["action"]["hash"], "#revision-configuration")
        self.assertIn("historical_source", {row["id"] for row in payload["checks"]})
        self.assertIn("runtime_storage", {row["id"] for row in payload["checks"]})

    @patch("src.backend.app.configuration_candidate", return_value=None)
    @patch("src.backend.app.approved_configuration", return_value=None)
    def test_backtest_missing_release_preserves_launch_contract(self, _approved, _candidate) -> None:
        payload = trading_historical_preflight(
            HistoricalPreflightRequest(
                mode="backtest",
                anchor_date=date(2026, 8, 18),
                session_count=5,
            )
        )

        self.assertFalse(payload["strategy_run_ready"])
        self.assertEqual(payload["window"]["sessions"], [])
        self.assertEqual(payload["available_run_plans"], [])

    @patch("src.backend.app.configuration_candidate", return_value=None)
    @patch("src.backend.app.approved_configuration", return_value=None)
    def test_debug_missing_release_is_a_blocked_readiness_payload(self, _approved, _candidate) -> None:
        payload = trading_backtest_debug_preflight(
            ReplayPreflightRequest(session_date=date(2026, 8, 18))
        )

        self.assertFalse(payload["ready"])
        self.assertEqual(payload["mode"], "backtest_debug")
        self.assertEqual(
            {row["id"] for row in payload["checks"]},
            {"approved_configuration", "runtime_storage"},
        )


if __name__ == "__main__":
    unittest.main()
