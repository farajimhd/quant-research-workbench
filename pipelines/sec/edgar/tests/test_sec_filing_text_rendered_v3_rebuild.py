from __future__ import annotations

import unittest
from datetime import UTC, date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from pipelines.sec.edgar.sec_filing_text_rendered_v3_rebuild import (
    SourceWatermark,
    build_rendered_row,
    load_or_create_run_manifest,
    staging_table_for_run,
)
from pipelines.market_sip.events.sec_packed_text_renderer import SEC_PACKED_TEXT_RENDERER_VERSION


class SecRenderedV3RebuildTest(unittest.TestCase):
    def test_staging_table_is_isolated_by_run(self) -> None:
        self.assertEqual(
            staging_table_for_run("sec-render/v8 20260716"),
            "sec_filing_text_rendered_stage_sec_render_v8_20260716_v3",
        )

    def test_rendered_row_preserves_source_lineage_and_uses_v8(self) -> None:
        now = datetime(2026, 7, 16, tzinfo=UTC)
        source = {
            "document_id": "doc",
            "filing_id": "filing",
            "accession_number": "0000000000-26-000001",
            "accession_number_compact": "000000000026000001",
            "cik": "0000000001",
            "text_kind": "primary_document",
            "source_archive_date": date(2026, 7, 1),
            "source_archive_member": "member.nc",
            "source_version_key": "version",
            "source_revision_at": now,
            "source_revision_rank": 123,
            "source_revision_kind": "daily_archive",
            "pac_event_id": None,
        }
        row = build_rendered_row(source, "Revenue: 100", ["format_html"], "run", now)
        self.assertEqual(row["normalizer_version"], SEC_PACKED_TEXT_RENDERER_VERSION)
        self.assertEqual(row["extraction_method"], SEC_PACKED_TEXT_RENDERER_VERSION)
        self.assertEqual(row["source_version_key"], "version")
        self.assertEqual(row["source_revision_rank"], 123)
        self.assertEqual(row["text_char_count"], len("Revenue: 100"))
        self.assertEqual(len(row["text_sha256"]), 64)

    def test_resume_rejects_source_watermark_change(self) -> None:
        args = SimpleNamespace(
            database="q_live",
            source_table="sec_filing_text_v3",
            target_table="sec_filing_text_rendered_v3",
            staging_table="sec_filing_text_rendered_stage_test_v3",
            manifest_table="sec_filing_text_rendered_rebuild_manifest_v3",
            workers=1,
        )
        original = SourceWatermark(10, 100, 7, "2026-07-16 00:00:00.000", 123)
        changed = SourceWatermark(11, 101, 8, "2026-07-16 00:01:00.000", 456)
        partitions = [{"partition_id": 202607, "source_rows": 10, "source_chars": 90}]
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            load_or_create_run_manifest(root, args, "test", [], original, partitions)
            with self.assertRaisesRegex(RuntimeError, "source changed since run started"):
                load_or_create_run_manifest(root, args, "test", [], changed, partitions)


if __name__ == "__main__":
    unittest.main()
