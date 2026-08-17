from __future__ import annotations

import unittest

from src.backend.market_discovery_runtime_service import MarketDiscoveryRuntimeCoordinator


class MarketDiscoveryRuntimeCoordinatorTests(unittest.TestCase):
    def test_active_market_refreshes_approved_discovery(self) -> None:
        calls: list[bool] = []
        runtime = MarketDiscoveryRuntimeCoordinator(
            health_loader=lambda: {"running": True, "status": "open", "market_calendar": {"active_collection_window": True}},
            configuration_loader=lambda: {"market_discovery": {"core_scan": {"refresh_interval_ms": 1500}}},
            refresh=lambda: calls.append(True) or {
                "core_population_count": 12000,
                "watchlist_runtime": {"watchlists": [{"watchlist_id": "one"}]},
                "signal_stream_runtime": {"signal_streams": [{"signal_stream_id": "one"}], "occurrence_count": 2},
            },
        )
        wait = runtime.refresh_once()
        self.assertEqual(calls, [True])
        self.assertEqual(wait, 1.5)
        self.assertEqual(runtime.snapshot()["core_population_count"], 12000)

    def test_closed_market_idles_without_refreshing(self) -> None:
        calls: list[bool] = []
        runtime = MarketDiscoveryRuntimeCoordinator(
            health_loader=lambda: {"running": True, "status": "closed", "market_calendar": {"active_collection_window": False}},
            configuration_loader=lambda: {"market_discovery": {}},
            refresh=lambda: calls.append(True) or {},
        )
        runtime.refresh_once()
        self.assertEqual(calls, [])
        self.assertEqual(runtime.snapshot()["state"], "market_idle")


if __name__ == "__main__":
    unittest.main()
