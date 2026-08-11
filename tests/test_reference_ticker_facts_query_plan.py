from __future__ import annotations

import unittest
from datetime import UTC, datetime

from src.backend.query_plans.reference_ticker_facts_v1 import identity_anchor
from src.backend.ticker_facts_service import identity_anchor_sql


class ReferenceTickerFactsQueryPlanTests(unittest.TestCase):
    def test_identity_anchor_is_causal_and_keeps_compatibility_import(self) -> None:
        cutoff = datetime(2026, 8, 11, 15, 45, 12, 123456, tzinfo=UTC)
        sql = identity_anchor("AAPL", cutoff, "q_live")
        self.assertEqual(sql, identity_anchor_sql("AAPL", cutoff, "q_live"))
        self.assertIn("universe_date <= toDate('2026-08-11')", sql)
        self.assertIn("inserted_at <= parseDateTime64BestEffort('2026-08-11T15:45:12.123+00:00')", sql)
        self.assertIn("s.first_seen_at_utc <= parseDateTime64BestEffort", sql)
        self.assertIn("upper(u.ticker) = 'AAPL'", sql)
        self.assertIn("LIMIT 1", sql)

    def test_identity_anchor_rejects_naive_cutoff(self) -> None:
        with self.assertRaisesRegex(ValueError, "timezone"):
            identity_anchor("AAPL", datetime(2026, 8, 11), "q_live")


if __name__ == "__main__":
    unittest.main()
