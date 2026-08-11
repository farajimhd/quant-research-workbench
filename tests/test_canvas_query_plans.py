from __future__ import annotations

import unittest
from datetime import UTC, datetime

from src.backend.query_plans import canvas_context_v1


class CanvasContextQueryPlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cutoff = datetime(2026, 8, 11, 15, 30, tzinfo=UTC)

    def test_news_plan_is_bounded_and_version_pinned(self) -> None:
        query = canvas_context_v1.company_news(
            self.cutoff,
            engine_version="engine-v1",
            synthesis_table="news_synthesis_v1",
        )

        self.assertIn("n.published_at_utc BETWEEN", query)
        self.assertIn("s.engine_version='engine-v1'", query)
        self.assertIn("LIMIT 30", query)
        self.assertIn("arrayDistinct", query)

    def test_scanner_plans_keep_company_and_filing_identity_scoped(self) -> None:
        news = canvas_context_v1.scanner_company_news(
            self.cutoff,
            engine_version="engine-v1",
            synthesis_table="news_synthesis_v1",
        )
        sec = canvas_context_v1.scanner_sec_filings(self.cutoff)

        self.assertIn("WHERE is_company_news", news)
        self.assertIn("GROUP BY ticker", news)
        self.assertIn("id_sec_market_bridge_v3", sec)
        self.assertIn("valid_to_date_exclusive", sec)
        self.assertIn("GROUP BY ticker", sec)

    def test_sec_identity_plan_quotes_and_deduplicates_ciks(self) -> None:
        query = canvas_context_v1.sec_ticker_identities(
            ["0000320193", "0000789019", "0000320193"]
        )

        self.assertEqual(query.count("'0000320193'"), 1)
        self.assertEqual(query.count("'0000789019'"), 1)
        self.assertIn("AS mapped_ticker", query)
        with self.assertRaisesRegex(ValueError, "at least one CIK"):
            canvas_context_v1.sec_ticker_identities([])


if __name__ == "__main__":
    unittest.main()
