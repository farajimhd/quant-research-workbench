from __future__ import annotations

import unittest
from datetime import UTC, datetime

from src.backend.feature_projection import compact_feature_projection


class CompactFeatureProjectionTests(unittest.TestCase):
    def test_projects_registry_provenance_and_compact_null_evidence(self) -> None:
        payload = compact_feature_projection(
            [
                {
                    "ticker": "AAPL",
                    "last": 201.5,
                    "market_cap": 3_000_000_000_000,
                    "market_cap_available_at": "2026-08-11T14:00:00+00:00",
                    "float_shares": None,
                    "float_shares_null_reason": "source_not_published",
                },
                {
                    "ticker": "MSFT",
                    "last": 505.0,
                    "market_cap": None,
                    "market_cap_null_reason": "not_yet_available",
                    "float_shares": 7_400_000_000,
                },
            ],
            as_of=datetime(2026, 8, 11, 15, 0, tzinfo=UTC),
            source_revision="scanner-42",
            source_schema_version=7,
        )

        self.assertEqual(payload["authority"], "application_field_registry")
        self.assertEqual(payload["row_count"], 2)
        self.assertEqual(payload["source_revision"], "scanner-42")
        self.assertEqual(payload["fields"]["symbol"]["coverage_pct"], 100.0)
        self.assertEqual(payload["fields"]["last_price"]["coverage_count"], 2)
        market_cap = payload["fields"]["market_cap"]
        self.assertEqual(market_cap["field_id"], "reference.market_cap")
        self.assertEqual(market_cap["query_plan_id"], "reference.scanner_asof.v1")
        self.assertEqual(market_cap["coverage_pct"], 50.0)
        self.assertEqual(market_cap["latest_available_at"], "2026-08-11T14:00:00+00:00")
        self.assertEqual(market_cap["null_reasons"], {"not_yet_available": 1})
        self.assertEqual(
            payload["fields"]["float_shares"]["null_reasons"],
            {"source_not_published": 1},
        )

    def test_empty_population_remains_explicit_without_fake_coverage(self) -> None:
        payload = compact_feature_projection([], as_of="2026-08-11T15:00:00Z")

        self.assertEqual(payload["row_count"], 0)
        self.assertEqual(payload["fields"]["market_cap"]["coverage_count"], 0)
        self.assertEqual(payload["fields"]["market_cap"]["coverage_pct"], 0.0)
        self.assertEqual(payload["fields"]["market_cap"]["null_reasons"], {})


if __name__ == "__main__":
    unittest.main()
