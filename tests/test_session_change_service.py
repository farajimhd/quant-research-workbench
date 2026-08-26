from __future__ import annotations

import unittest
from datetime import UTC, datetime

from src.backend.query_plans.reference_scanner_asof_v1 import scanner_reference_projection
from src.backend.session_change_service import session_change_projection


class SessionChangeServiceTests(unittest.TestCase):
    def test_reference_plan_selects_ratio_from_the_same_nearest_split(self) -> None:
        sql = scanner_reference_projection(datetime(2026, 8, 26, tzinfo=UTC))

        self.assertIn("AS selected_execution_date", sql)
        self.assertIn("AS split_from", sql)
        self.assertIn("AS split_to", sql)

    def test_reverse_split_converts_prior_close_to_current_share_units(self) -> None:
        result = session_change_projection(
            current_price=9.0284,
            raw_previous_close=0.7287,
            expected_previous_session_date="2026-08-25",
            reference_previous_session_date="2026-08-25",
            session_date="2026-08-26",
            previous_close_source="qmd_live_intraday_family_bars_v3",
            split_execution_date="2026-08-26",
            split_from=10,
            split_to=1,
        )

        self.assertAlmostEqual(result["previous_close"], 7.287)
        self.assertAlmostEqual(result["change_pct"], 23.8973514478)
        self.assertAlmostEqual(result["change_actual"], 1.7414)
        self.assertTrue(result["split_adjusted"])
        self.assertEqual(result["session_change_adjustment_factor"], 10)

    def test_future_split_does_not_adjust_earlier_session(self) -> None:
        result = session_change_projection(
            current_price=11,
            raw_previous_close=10,
            expected_previous_session_date="2026-08-25",
            reference_previous_session_date="2026-08-25",
            session_date="2026-08-26",
            split_execution_date="2026-08-27",
            split_from=10,
            split_to=1,
        )

        self.assertEqual(result["previous_close"], 10)
        self.assertAlmostEqual(result["change_pct"], 10)
        self.assertFalse(result["split_adjusted"])

    def test_mismatched_reference_session_fails_closed(self) -> None:
        result = session_change_projection(
            current_price=11,
            raw_previous_close=10,
            expected_previous_session_date="2026-08-25",
            reference_previous_session_date="2026-08-22",
            session_date="2026-08-26",
        )

        self.assertIsNone(result["change_pct"])
        self.assertEqual(result["previous_close_reference_status"], "stale")

    def test_missing_corporate_action_authority_fails_closed(self) -> None:
        result = session_change_projection(
            current_price=11,
            raw_previous_close=10,
            expected_previous_session_date="2026-08-25",
            reference_previous_session_date="2026-08-25",
            session_date="2026-08-26",
            corporate_action_reference_status="unavailable",
        )

        self.assertIsNone(result["change_pct"])
        self.assertEqual(
            result["previous_close__null_reason"],
            "corporate_action_reference_unavailable",
        )


if __name__ == "__main__":
    unittest.main()
