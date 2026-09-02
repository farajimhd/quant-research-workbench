from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKTEST_PAGE = ROOT / "frontend" / "src" / "pages" / "HistoricalTradingPage.tsx"


class FrontendBacktestLaunchContractTests(unittest.TestCase):
    def test_backtest_launch_exposes_session_and_ticker_scope(self) -> None:
        source = BACKTEST_PAGE.read_text(encoding="utf-8")

        self.assertIn("Whole extended session · 04:00–20:00 ET", source)
        self.assertIn("Premarket · 04:00–09:30 ET", source)
        self.assertIn("All tickers eligible under the Run Plan", source)
        self.assertIn("One ticker only", source)
        self.assertIn('tickers: tickerScope === "single" ? [normalizedTicker] : []', source)
        self.assertIn("simulation_profile: simulationProfile", source)
        self.assertIn("end_time: endTime", source)

    def test_active_run_reports_accelerated_engine_progress(self) -> None:
        source = BACKTEST_PAGE.read_text(encoding="utf-8")

        self.assertIn('role="progressbar"', source)
        self.assertIn("exact events", source)
        self.assertIn("Through {formatReplayTime(run.current_time)} ET", source)
        self.assertIn("Accelerated causal engine", source)


if __name__ == "__main__":
    unittest.main()
