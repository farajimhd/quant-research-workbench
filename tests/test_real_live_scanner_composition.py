from __future__ import annotations

import threading
import unittest
from unittest.mock import patch

from src.backend.bounded_cache import BoundedSingleFlightTtlCache
from src.backend import real_live_trading_service as service


class RealLiveScannerCompositionTests(unittest.TestCase):
    def test_interval_sources_load_concurrently_and_preserve_configuration_order(self) -> None:
        barrier = threading.Barrier(3, timeout=1)

        def indicator_loader(*, timeframe: str, row_limit: int):
            self.assertEqual(row_limit, 25_000)
            barrier.wait()
            return [{"ticker": timeframe.upper()}]

        def macro_loader(*, timeframe: str, row_limit: int):
            self.assertEqual(row_limit, 25_000)
            barrier.wait()
            return [{"ticker": timeframe.upper()}]

        rows = service.load_discovery_interval_sources(
            ("5m", "1d", "100ms"),
            indicator_loader=indicator_loader,
            macro_loader=macro_loader,
        )

        self.assertEqual([interval for interval, _ in rows], ["5m", "1d", "100ms"])
        self.assertEqual([source[0]["ticker"] for _, source in rows], ["5M", "1D", "100MS"])

    def test_interval_merge_never_replaces_session_fields_with_raw_bar_values(self) -> None:
        merged = service.merge_interval_field_instances(
            {"volume": 6_800_000.0, "vwap": 340.25},
            {
                "ticker": "TSLA",
                "volume": 5_456.0,
                "vwap": 338.97,
                "data.price_change_pct@1:value@@3m": 0.25,
            },
        )
        self.assertEqual(merged["volume"], 6_800_000.0)
        self.assertEqual(merged["vwap"], 340.25)
        self.assertEqual(merged["data.price_change_pct@1:value@@3m"], 0.25)

    def test_full_population_is_cached_before_per_request_slicing(self) -> None:
        cache = BoundedSingleFlightTtlCache[str, dict](
            max_entries=1,
            ttl_seconds=60,
            contract_revision="scanner-composition.test",
        )
        complete = {
            "provider": "qmd-gateway",
            "source_revision": "qmd-42",
            "schema_version": 2,
            "core_population_count": 3,
            "rows": [
                {"ticker": "AAPL"},
                {"ticker": "MSFT"},
                {"ticker": "NVDA"},
            ],
        }
        with (
            patch.object(service, "SCANNER_COMPOSITION_CACHE", cache),
            patch.object(
                service,
                "_compose_real_live_scanner_snapshot",
                return_value=complete,
            ) as compose,
        ):
            first = service.real_live_scanner_snapshot(row_limit=1)
            second = service.real_live_scanner_snapshot(row_limit=2)

        compose.assert_called_once_with(allow_provider_fallback=False)
        self.assertEqual(first["row_count"], 1)
        self.assertEqual(second["row_count"], 2)
        self.assertEqual(second["core_population_count"], 3)
        self.assertEqual(second["source_revision"], "qmd-42")
        self.assertEqual(second["feature_projection"]["row_count"], 2)

    def test_cold_presentation_request_returns_building_state_without_waiting(self) -> None:
        cache = BoundedSingleFlightTtlCache[str, dict](
            max_entries=2,
            ttl_seconds=60,
            contract_revision="scanner-composition.test",
        )
        with (
            patch.object(service, "SCANNER_COMPOSITION_CACHE", cache),
            patch.object(service, "SCANNER_LATEST_COMPLETE", None),
            patch.object(service, "SCANNER_REFRESH_ERROR", ""),
            patch.object(service, "SCANNER_REFRESH_GENERATION", None),
            patch.object(service, "SCANNER_CONFIGURATION_GENERATION", 17),
            patch.object(service.threading, "Thread") as thread,
        ):
            payload = service.real_live_scanner_snapshot(row_limit=250)

        self.assertEqual(payload["composition_status"], "building")
        self.assertEqual(payload["rows"], [])
        thread.return_value.start.assert_called_once_with()

    def test_expired_presentation_request_serves_last_complete_snapshot_while_refreshing(self) -> None:
        cache = BoundedSingleFlightTtlCache[str, dict](
            max_entries=2,
            ttl_seconds=60,
            contract_revision="scanner-composition.test",
        )
        previous = {"provider": "qmd-gateway", "rows": [{"ticker": "AAPL"}, {"ticker": "MSFT"}]}
        with (
            patch.object(service, "SCANNER_COMPOSITION_CACHE", cache),
            patch.object(service, "SCANNER_LATEST_COMPLETE", previous),
            patch.object(service, "SCANNER_REFRESH_ERROR", ""),
            patch.object(service, "SCANNER_REFRESH_GENERATION", None),
            patch.object(service, "SCANNER_CONFIGURATION_GENERATION", 19),
            patch.object(service.threading, "Thread") as thread,
        ):
            payload = service.real_live_scanner_snapshot(row_limit=1)

        self.assertEqual(payload["composition_status"], "refreshing")
        self.assertEqual(payload["rows"], [{"ticker": "AAPL"}])
        thread.return_value.start.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
