from __future__ import annotations

import unittest
from datetime import UTC, datetime

from src.backend.sec_canvas_service import (
    classify_sec_filing,
    detail_facts_sql,
    detail_filing_entities_sql,
    detail_source_text_metadata_sql,
    detail_source_text_page_sql,
    detail_text_metadata_sql,
    detail_text_page_sql,
    filing_list_sql,
    normalize_accession,
    normalize_cik,
    normalize_clickhouse_utc,
    normalize_sec_filing_row,
    parse_as_of,
    sec_document_text_payload,
)


class SecCanvasServiceTests(unittest.TestCase):
    def test_unmapped_forms_use_one_explicit_fallback(self) -> None:
        result = classify_sec_filing("UNKNOWN-FORM")
        self.assertEqual(result["filing_label"], "other_disclosure")
        self.assertIn("no approved", result["label_evidence"][0])

    def test_point_in_time_query_filters_content_before_pagination(self) -> None:
        cutoff = datetime(2026, 7, 18, 14, 0, tzinfo=UTC)
        sql = filing_list_sql(cutoff=cutoff, database="q_live", label="results", limit=101, lookback_hours=168, search="Apple", ticker="AAPL", before=None, before_accession="", content="readable")
        self.assertIn("sec_filing_text_rendered_v3", sql)
        self.assertIn("source_revision_at <=", sql)
        self.assertIn("accepted_at_utc <=", sql)
        self.assertNotIn("f.inserted_at <=", sql)
        self.assertIn("sec_filing_entity_v3", sql)
        self.assertIn("entity_role IN ('issuer', 'subject_company')", sql)
        self.assertIn("valid_from_date", sql)
        self.assertIn("sec_disclosure_taxonomy_v3", sql)
        self.assertIn("WHERE filing_label = 'results'", sql)
        self.assertLess(sql.index("sec_filing_text_rendered_v3"), sql.index("LIMIT 101"))
        self.assertIn("ORDER BY accepted_at_utc DESC, accession_number DESC", sql)

    def test_detail_text_is_metadata_first_and_bounded(self) -> None:
        cutoff = datetime(2026, 7, 18, 14, 0, tzinfo=UTC)
        metadata = detail_text_metadata_sql("0000320193", "0000320193-25-000079", cutoff, "q_live")
        page = detail_text_page_sql("0000320193", "0000320193-25-000079", "doc-1", cutoff, "q_live", limit=32000, offset=64000)
        self.assertNotIn("argMax(text,", metadata)
        self.assertIn("argMax(text_char_count", metadata)
        self.assertIn("substringUTF8(argMax(text", page)
        self.assertIn(", 64001, 32000)", page)
        self.assertIn("source_revision_at <=", page)

        original_metadata = detail_source_text_metadata_sql("0000320193", "0000320193-25-000079", cutoff, "q_live")
        original_page = detail_source_text_page_sql("0000320193", "0000320193-25-000079", "doc-1", cutoff, "q_live", limit=16000, offset=32000)
        self.assertNotIn("argMax(source_text,", original_metadata)
        self.assertIn("argMax(source_text_char_count", original_metadata)
        self.assertIn("substringUTF8(argMax(source_text", original_page)
        self.assertIn(", 32001, 16000)", original_page)

    def test_detail_entities_include_all_filing_roles(self) -> None:
        cutoff = datetime(2026, 7, 18, 14, 0, tzinfo=UTC)
        sql = detail_filing_entities_sql("0000320193", "0000320193-25-000079", cutoff, "q_live")
        self.assertIn("entity_name", sql)
        self.assertIn("entity_role", sql)
        self.assertNotIn("entity_role IN", sql)
        self.assertIn("source_revision_at <=", sql)

    def test_document_view_rejects_unknown_source(self) -> None:
        with self.assertRaisesRegex(ValueError, "rendered or original"):
            sec_document_text_payload("0000320193", "0000320193-25-000079", "doc-1", view="raw")

    def test_xbrl_availability_uses_filing_date_and_pages(self) -> None:
        cutoff = datetime(2026, 7, 18, 14, 0, tzinfo=UTC)
        sql = detail_facts_sql("0000320193", "0000320193-25-000079", cutoff, "q_live", limit=101, offset=200)
        self.assertIn("filed_at_utc <=", sql)
        self.assertNotIn("recorded_at_utc <=", sql)
        self.assertIn("LIMIT 101 OFFSET 200", sql)

    def test_identifiers_and_clock_are_strict(self) -> None:
        self.assertEqual(normalize_cik("320193"), "0000320193")
        self.assertEqual(normalize_accession("0000320193-25-000079"), "0000320193-25-000079")
        with self.assertRaises(ValueError):
            parse_as_of("2026-07-18T09:45:00")
        self.assertEqual(parse_as_of("2026-07-18T09:45:00-04:00").tzinfo, UTC)
        self.assertEqual(normalize_clickhouse_utc("2026-06-17 22:40:43.000000000"), "2026-06-17T22:40:43.000Z")

    def test_public_filing_rows_always_expose_list_items(self) -> None:
        scalar = normalize_sec_filing_row({"form_type": "8-K", "items": "8.01, 9.01"})
        self.assertEqual(scalar["items"], ["8.01", "9.01"])
        self.assertEqual(scalar["filing_label"], "other_disclosure")
        self.assertIn("Items 8.01, 9.01", scalar["label_evidence"])

        missing = normalize_sec_filing_row({"form_type": "D", "items": None})
        self.assertEqual(missing["items"], [])

        date_only = normalize_sec_filing_row({"form_type": "D", "items": None, "accepted_at_source": "archive_filing_date_midnight"})
        self.assertEqual(date_only["event_time_quality"], "date_only")


if __name__ == "__main__":
    unittest.main()
