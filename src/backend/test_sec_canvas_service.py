from __future__ import annotations

import unittest
from datetime import UTC, datetime

from src.backend.sec_canvas_service import classify_sec_filing, filing_list_sql, normalize_accession, normalize_cik, parse_as_of


class SecCanvasServiceTests(unittest.TestCase):
    def test_initial_form_taxonomy(self) -> None:
        expected = {
            "10-K": "results", "8-K": "material_event", "4": "insider_ownership",
            "SC 13G": "holder_ownership", "S-3": "offering_capital", "DEF 14A": "governance_proxy",
            "DEFM14A": "merger_tender", "NT 10-Q": "compliance_notice", "N-PORT": "fund_disclosure",
        }
        for form, label in expected.items():
            with self.subTest(form=form):
                self.assertEqual(classify_sec_filing(form)["filing_label"], label)

    def test_point_in_time_query_filters_content_before_pagination(self) -> None:
        cutoff = datetime(2026, 7, 18, 14, 0, tzinfo=UTC)
        sql = filing_list_sql(cutoff=cutoff, database="q_live", label="results", limit=101, lookback_hours=168, search="Apple", ticker="AAPL", before=None, before_accession="", content="readable")
        self.assertIn("sec_filing_text_rendered_v3 FINAL", sql)
        self.assertIn("accepted_at_utc <=", sql)
        self.assertIn("inserted_at <=", sql)
        self.assertIn("WHERE filing_label = 'results'", sql)
        self.assertLess(sql.index("sec_filing_text_rendered_v3 FINAL"), sql.index("LIMIT 101"))
        self.assertIn("ORDER BY accepted_at_utc DESC, accession_number DESC", sql)

    def test_identifiers_and_clock_are_strict(self) -> None:
        self.assertEqual(normalize_cik("320193"), "0000320193")
        self.assertEqual(normalize_accession("0000320193-25-000079"), "0000320193-25-000079")
        with self.assertRaises(ValueError):
            parse_as_of("2026-07-18T09:45:00")
        self.assertEqual(parse_as_of("2026-07-18T09:45:00-04:00").tzinfo, UTC)


if __name__ == "__main__":
    unittest.main()
