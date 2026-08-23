from __future__ import annotations

import unittest
from unittest.mock import patch

from src.backend.market_discovery_runtime_service import MarketDiscoveryRuntimeCoordinator


class MarketDiscoveryRuntimeCoordinatorTests(unittest.TestCase):
    def test_active_market_refreshes_approved_discovery(self) -> None:
        calls: list[bool] = []
        runtime = MarketDiscoveryRuntimeCoordinator(
            health_loader=lambda: {"running": True, "status": "open", "market_calendar": {"active_collection_window": True}},
            configuration_loader=lambda: {"market_discovery": {"core_scan": {"refresh_interval_ms": 1500}}},
            refresh=lambda: calls.append(True) or {
                "core_population_count": 12000,
                "stage_durations_ms": {"core_snapshot": 12.5},
                "watchlist_runtime": {"watchlists": [{"watchlist_id": "one"}]},
                "signal_stream_runtime": {"signal_streams": [{"signal_stream_id": "one"}], "occurrence_count": 2},
            },
        )
        wait = runtime.refresh_once()
        self.assertEqual(calls, [True])
        self.assertGreater(wait, 0)
        self.assertLessEqual(wait, 1.5)
        self.assertEqual(runtime.snapshot()["core_population_count"], 12000)
        self.assertEqual(runtime.snapshot()["stage_durations_ms"]["core_snapshot"], 12.5)

    @patch("src.backend.market_discovery_runtime_service.time.perf_counter", side_effect=[10.0, 12.0])
    def test_overrunning_cycle_restarts_without_an_extra_refresh_interval(self, _clock) -> None:
        runtime = MarketDiscoveryRuntimeCoordinator(
            health_loader=lambda: {"running": True, "status": "open", "market_calendar": {"active_collection_window": True}},
            configuration_loader=lambda: {"market_discovery": {"core_scan": {"refresh_interval_ms": 1000}}},
            refresh=lambda: {},
        )

        self.assertEqual(runtime.refresh_once(), 0.01)

    def test_closed_market_prewarms_observation_once_then_idles(self) -> None:
        calls: list[bool] = []
        runtime = MarketDiscoveryRuntimeCoordinator(
            health_loader=lambda: {"running": True, "status": "closed", "market_calendar": {"active_collection_window": False}},
            configuration_loader=lambda: {"market_discovery": {}},
            refresh=lambda: calls.append(True) or {
                "core_population_count": 2530,
                "observation": {"session_date": "2026-08-21"},
            },
        )
        runtime.refresh_once()
        runtime.refresh_once()
        snapshot = runtime.snapshot()
        self.assertEqual(calls, [True])
        self.assertEqual(snapshot["state"], "market_idle")
        self.assertEqual(snapshot["closed_observation_state"], "ready")
        self.assertEqual(snapshot["closed_observation_session"], "2026-08-21")
        self.assertEqual(snapshot["core_population_count"], 2530)


if __name__ == "__main__":
    unittest.main()
