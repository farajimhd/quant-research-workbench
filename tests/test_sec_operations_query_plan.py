from __future__ import annotations

import unittest
from datetime import UTC, datetime

from src.backend.query_plans.sec_operations_v1 import intraday_histogram


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


if __name__ == "__main__":
    unittest.main()
