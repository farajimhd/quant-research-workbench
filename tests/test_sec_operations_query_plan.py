from __future__ import annotations

import unittest
from datetime import UTC, datetime

from src.backend.query_plans.sec_operations_v1 import (
    intraday_histogram,
    identity_rows_by_cik,
    related_filing_counts,
    today_filings,
    today_summary,
)


class SecOperationsQueryPlanTests(unittest.TestCase):
    def test_histogram_is_bounded_and_classifies_each_filing_once(self) -> None:
        sql = intraday_histogram(
            datetime(2026, 8, 11, 4, tzinfo=UTC),
            datetime(2026, 8, 12, 4, tzinfo=UTC),
            bin_seconds=300,
        )

        self.assertIn("FROM `q_live`.`sec_filing_v3`", sql)
        self.assertIn("accepted_at_utc >= window_start", sql)
        self.assertIn("accepted_at_utc < window_end", sql)
        self.assertIn("(toString(cik), accession_number) IN", sql)
        self.assertIn("related_xbrl_rows > 0", sql)
        self.assertIn("related_text_rows > 0", sql)
        self.assertIn("related_document_rows > 0", sql)
        self.assertIn("FROM numbers(289)", sql)

    def test_today_bundle_is_bounded_and_set_based(self) -> None:
        start = datetime(2026, 8, 11, 4, tzinfo=UTC)
        end = datetime(2026, 8, 12, 4, tzinfo=UTC)
        summary = today_summary(start, end)
        filings = today_filings(start, end, limit=50_000, ascending=False)
        related = related_filing_counts(
            [("123", "0001"), ("123", "0001"), ("456", "0002")]
        )

        self.assertIn("total_filings", summary)
        self.assertIn("LIMIT 1000", filings)
        self.assertIn("ORDER BY f.accepted_at_utc DESC", filings)
        self.assertEqual(
            set(related), {"documents", "texts", "company_facts", "frames"}
        )
        for sql in related.values():
            self.assertIn("('123', '0001')", sql)
            self.assertIn("('456', '0002')", sql)
            self.assertIn("GROUP BY cik, accession_number", sql)

    def test_empty_related_key_set_executes_no_query(self) -> None:
        self.assertEqual(related_filing_counts([]), {})

    def test_identity_query_is_bounded_and_preserves_primary_order(self) -> None:
        sql = identity_rows_by_cik(["456", "123", "123"])

        self.assertIn("WHERE b.cik IN ('123', '456')", sql)
        self.assertIn("sym.primary_symbol_flag DESC", sql)
        self.assertIn("listing.is_primary_listing DESC", sql)
        self.assertIn("b.confidence_score DESC", sql)
        with self.assertRaises(ValueError):
            identity_rows_by_cik([])


if __name__ == "__main__":
    unittest.main()
