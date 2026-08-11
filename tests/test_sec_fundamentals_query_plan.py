from __future__ import annotations

import unittest
from datetime import UTC, datetime

from src.backend.query_plans.sec_fundamentals_asof_v1 import (
    fundamental_fact_queries,
    fundamental_history,
    scanner_fundamentals,
)
from src.backend.ticker_facts_service import (
    FUNDAMENTAL_TAGS,
    fundamental_history_sql,
    fundamentals_history_sql,
    fundamentals_sql,
)


class SecFundamentalsQueryPlanTests(unittest.TestCase):
    def test_bundle_is_causal_bounded_and_matches_compatibility_functions(self) -> None:
        cutoff = datetime(2026, 8, 11, 15, 45, 12, 123456, tzinfo=UTC)
        tags = sorted({tag for _, alternatives in FUNDAMENTAL_TAGS for tag in alternatives})
        queries = fundamental_fact_queries(
            cik="0000320193",
            tags=tags,
            cutoff=cutoff,
            database="q_live",
        )
        self.assertEqual(queries["current"], fundamentals_sql("0000320193", cutoff, "q_live"))
        self.assertEqual(queries["history"], fundamentals_history_sql("0000320193", cutoff, "q_live"))
        for sql in queries.values():
            self.assertIn("filed_at_utc <= parseDateTime64BestEffort", sql)
            self.assertIn("recorded_at_utc <= parseDateTime64BestEffort", sql)
            self.assertIn("sec_xbrl_company_fact_v3 FINAL", sql)

    def test_one_tag_history_is_bounded_and_matches_compatibility_function(self) -> None:
        cutoff = datetime(2026, 8, 11, tzinfo=UTC)
        sql = fundamental_history("0000320193", "Revenue", cutoff, "q_live", limit=50_000)
        self.assertEqual(
            sql,
            fundamental_history_sql("0000320193", "Revenue", cutoff, "q_live", limit=50_000),
        )
        self.assertIn("LIMIT 10000", sql)

    def test_cutoff_and_tag_catalog_fail_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "timezone"):
            fundamental_fact_queries(
                cik="0000320193",
                tags=("Revenue",),
                cutoff=datetime(2026, 8, 11),
                database="q_live",
            )

    def test_scanner_query_is_set_based_and_causal(self) -> None:
        sql = scanner_fundamentals(
            ("Revenue", "NetIncomeLoss"),
            datetime(2026, 8, 11, 15, 45, tzinfo=UTC),
            "q_live",
        )
        self.assertIn("latest_universe_date", sql)
        self.assertIn("INNER JOIN universe", sql)
        self.assertIn("u.inserted_at <= cutoff", sql)
        self.assertIn("f.filed_at_utc <= cutoff", sql)
        self.assertIn("f.recorded_at_utc <= cutoff", sql)
        self.assertIn("LIMIT 8 BY ticker, tag", sql)
        with self.assertRaisesRegex(ValueError, "at least one"):
            fundamental_fact_queries(
                cik="0000320193",
                tags=(),
                cutoff=datetime(2026, 8, 11, tzinfo=UTC),
                database="q_live",
            )


if __name__ == "__main__":
    unittest.main()
