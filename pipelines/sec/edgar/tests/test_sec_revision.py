from __future__ import annotations

import unittest
from argparse import Namespace
from datetime import date
from pathlib import Path

from pipelines.sec.edgar.sec_filing_text_clickhouse_file_ingest import PartFile, insert_sql
from pipelines.sec.edgar.sec_pipeline.clickhouse_writer import validate_source_lineage

from pipelines.sec.edgar.sec_pipeline.revision import (
    parse_pac_event,
    select_authoritative_candidate,
    source_revision,
)


class SecRevisionTests(unittest.TestCase):
    def test_parses_pac_deletion_and_document_type_change(self) -> None:
        payload = """<SUBMISSION>
<CORRECTION>
<TIMESTAMP>20240920153045
<ACCESSION-NUMBER>0000123456-24-000001
<TYPE>8-K
<FILING-DATE>20240919
<DATE-OF-FILING-DATE-CHANGE>20240920
<CIK>123456
<DOCUMENT>
<TYPE>EX-99.1
<SEQUENCE>2
<FILENAME>release.htm
<DELETION>
</DOCUMENT>
</SUBMISSION>"""
        event = parse_pac_event(
            payload,
            archive_date=date(2024, 9, 20),
            archive_member="0000123456-24-000001.pc",
            archive_path="20240920.nc.tar.gz",
            source_content_sha256="abc",
        )
        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.correction_timestamp_raw, "20240920153045")
        self.assertEqual(event.correction_order_key, 558450000)
        self.assertEqual(event.date_as_of_change, "2024-09-20")
        self.assertFalse(event.filing_deleted)
        self.assertEqual(len(event.document_changes), 1)
        self.assertTrue(event.document_changes[0].deleted)
        self.assertEqual(event.rows(source_run_id="test", inserted_at="2024-09-20 16:00:00.000")[0]["action"], "document_deleted")

    def test_ordinary_submission_is_not_pac(self) -> None:
        payload = """<SUBMISSION>
<ACCESSION-NUMBER>0000123456-24-000001
<DATE-OF-FILING-DATE-CHANGE>20240920
<DOCUMENT><TYPE>8-K<SEQUENCE>1<FILENAME>main.htm<TEXT></TEXT></DOCUMENT>
</SUBMISSION>"""
        self.assertIsNone(
            parse_pac_event(
                payload,
                archive_date=date(2024, 9, 20),
                archive_member="filing.nc",
                archive_path="archive.tar.gz",
                source_content_sha256="abc",
            )
        )

    def test_source_version_is_deterministic_and_source_chronological(self) -> None:
        earlier = source_revision(
            archive_date="2024-09-16",
            archive_member="filing.nc",
            archive_path="20240916.nc.tar.gz",
            source_content_sha256="empty",
        )
        later = source_revision(
            archive_date="2024-09-23",
            archive_member="filing.nc",
            archive_path="20240923.nc.tar.gz",
            source_content_sha256="complete",
        )
        self.assertEqual(earlier.source_revision_at, "2024-09-16 00:00:00.000")
        self.assertNotEqual(earlier.source_version_key, later.source_version_key)
        self.assertEqual(
            later.source_version_key,
            source_revision(
                archive_date="2024-09-23",
                archive_member="filing.nc",
                archive_path="20240923.nc.tar.gz",
                source_content_sha256="complete",
            ).source_version_key,
        )

    def test_authoritative_candidate_ignores_arrival_timestamp(self) -> None:
        winner = select_authoritative_candidate(
            [
                {
                    "source_archive_date": "2024-09-23",
                    "source_text_byte_count": 200,
                    "content_sha256": "new",
                    "inserted_at": "2024-09-23 01:00:00",
                },
                {
                    "source_archive_date": "2024-09-16",
                    "source_text_byte_count": 0,
                    "content_sha256": "old",
                    "inserted_at": "2026-07-12 20:00:00",
                },
            ]
        )
        self.assertEqual(winner["content_sha256"], "new")

    def test_latest_deletion_is_authoritative_tombstone(self) -> None:
        winner = select_authoritative_candidate(
            [
                {"source_revision_rank": 10, "source_text_byte_count": 200, "content_sha256": "old"},
                {"source_revision_rank": 20, "document_deleted": 1, "content_sha256": "delete"},
            ]
        )
        self.assertEqual(winner["content_sha256"], "delete")

    def test_rejects_rendered_row_without_raw_source(self) -> None:
        document = {"cik": "1", "accession_number": "a", "document_id": "d"}
        rendered = {"cik": "1", "accession_number": "a", "document_id": "d"}
        with self.assertRaisesRegex(RuntimeError, "rendered_without_source=1"):
            validate_source_lineage([document], [], [rendered])

    def test_historical_insert_uses_revision_authority(self) -> None:
        part = PartFile(
            run_id="run",
            dataset_name="text",
            target_table="sec_filing_text_rendered_v3",
            part_index=1,
            windows_path=Path("part.parquet"),
            clickhouse_path="/mnt/d/part.parquet",
            expected_rows=1,
            expected_bytes=1,
            columns=["cik", "accession_number", "document_id", "text_kind", "source_revision_rank", "source_version_key", "inserted_at"],
            structure="",
        )
        sql = insert_sql(Namespace(database="q_live"), part)
        self.assertIn("ORDER BY source_revision_rank DESC", sql)
        self.assertIn("current_revision_rank", sql)
        self.assertIn("now64(3) AS inserted_at", sql)
        self.assertIn("source_authority", sql)
        self.assertIn("i.source_version_key = a.authority_version_key", sql)


if __name__ == "__main__":
    unittest.main()
