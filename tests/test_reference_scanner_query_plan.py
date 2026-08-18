from __future__ import annotations

import unittest
from datetime import UTC, datetime

from src.backend.query_plans.reference_scanner_asof_v1 import (
    scanner_reference_projection,
)


class ReferenceScannerQueryPlanTests(unittest.TestCase):
    def test_plan_is_set_based_and_applies_all_availability_cutoffs(self) -> None:
        sql = scanner_reference_projection(
            datetime(2026, 8, 11, 15, 45, 12, 123456, tzinfo=UTC),
            "q_live",
        )
        self.assertIn("is_tradable = 1", sql)
        self.assertNotIn("ticker IN", sql)
        self.assertIn("feature_date <= cutoff_date AND inserted_at <= cutoff", sql)
        self.assertIn("asset_kind = 'logo' AND status = 'active' AND inserted_at <= cutoff", sql)
        self.assertIn("published_at_utc", sql)
        self.assertIn("FROM `q_live`.market_ipo_v1 FINAL", sql)
        self.assertIn("FROM `q_live`.market_stock_split_v1 FINAL", sql)
        self.assertIn("AS float_quality", sql)
        self.assertIn("AS short_interest_pct", sql)
        self.assertIn("FROM `q_live`.market_short_volume_v1 FINAL", sql)
        self.assertIn("FROM `q_live`.market_fails_to_deliver_v1 FINAL", sql)
        self.assertIn("FROM `q_live`.market_reg_sho_threshold_v1 FINAL", sql)
        self.assertIn("FROM `q_live`.market_security_borrow_v1 FINAL", sql)
        self.assertIn("observed_at_utc <= cutoff AND inserted_at <= cutoff", sql)
        self.assertIn("SETTINGS join_use_nulls = 1", sql)
        self.assertIn("2026-08-11T15:45:12.123+00:00", sql)

    def test_plan_rejects_naive_cutoff(self) -> None:
        with self.assertRaisesRegex(ValueError, "timezone"):
            scanner_reference_projection(datetime(2026, 8, 11))

    def test_plan_can_bound_the_projection_to_a_visible_page(self) -> None:
        sql = scanner_reference_projection(
            datetime(2026, 8, 11, tzinfo=UTC),
            tickers=("msft", "AAPL", "AAPL"),
        )
        self.assertIn("upper(ticker) IN ('AAPL', 'MSFT')", sql)


if __name__ == "__main__":
    unittest.main()
