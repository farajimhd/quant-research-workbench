from __future__ import annotations

import unittest
from unittest.mock import patch

from src.backend.bounded_cache import BoundedSingleFlightTtlCache
from src.backend import real_live_trading_service as service


class RealLiveScannerCompositionTests(unittest.TestCase):
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

        compose.assert_called_once_with()
        self.assertEqual(first["row_count"], 1)
        self.assertEqual(second["row_count"], 2)
        self.assertEqual(second["core_population_count"], 3)
        self.assertEqual(second["source_revision"], "qmd-42")
        self.assertEqual(second["feature_projection"]["row_count"], 2)


if __name__ == "__main__":
    unittest.main()
