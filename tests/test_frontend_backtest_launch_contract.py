from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKTEST_PAGE = ROOT / "frontend" / "src" / "pages" / "HistoricalTradingPage.tsx"


class FrontendBacktestLaunchContractTests(unittest.TestCase):
    def test_backtest_launch_exposes_focused_ticker_date_and_period(self) -> None:
        source = BACKTEST_PAGE.read_text(encoding="utf-8")

        self.assertIn("Whole extended session · 04:00–20:00 ET", source)
        self.assertIn("Premarket · 04:00–09:30 ET", source)
        self.assertIn("Regular session · 09:30–16:00 ET", source)
        self.assertIn("Custom period", source)
        self.assertIn("Trading date", source)
        self.assertIn("Start time · ET", source)
        self.assertIn("End time · ET", source)
        self.assertIn("tickers: [normalizedTicker]", source)
        self.assertIn("tickers: normalizedTicker ? [normalizedTicker] : []", source)
        self.assertIn("configuration/candidates?latest_only=true", source)
        self.assertIn("session_count: 1", source)
        self.assertIn("simulation_profile: simulationProfile", source)
        self.assertIn("start_time: startTime", source)
        self.assertIn("end_time: endTime", source)
        self.assertNotIn("<span>Test Candidate</span>", source)
        self.assertNotIn("<span>Strategy Run Plan</span>", source)
        self.assertNotIn("Prior exchange sessions", source)
        self.assertNotIn("Backtest universe", source)

    def test_strategy_authority_is_automatic_but_visible(self) -> None:
        source = BACKTEST_PAGE.read_text(encoding="utf-8")

        self.assertIn("strategy selected automatically", source)
        self.assertIn("Immutable strategy revision", source)
        self.assertIn("one-second Charts &amp; Quotes view", source)
        self.assertIn("MACD, strategy positions, lifecycle activity, and performance", source)

    def test_active_run_reports_accelerated_engine_progress(self) -> None:
        source = BACKTEST_PAGE.read_text(encoding="utf-8")

        self.assertIn('role="progressbar"', source)
        self.assertIn("exact events", source)
        self.assertIn("Through {formatReplayTime(run.current_time)} ET", source)
        self.assertIn("Accelerated causal engine", source)


if __name__ == "__main__":
    unittest.main()
