from __future__ import annotations

import unittest
import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from pipelines.sec.edgar.sec_filing_text_rendered_v3_rebuild import (
    SourceWatermark,
    FilingWatermark,
    build_rendered_row,
    load_or_create_run_manifest,
    load_filing_forms,
    load_partition_authority,
    staging_table_for_run,
)
from pipelines.sec.edgar.sec_pipeline.text_renderer import SEC_PACKED_TEXT_RENDERER_VERSION


class SecRenderedV3RebuildTest(unittest.TestCase):
    def test_partition_form_lookup_uses_compact_local_authority(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            map_path = root / "forms.sqlite"
            connection = sqlite3.connect(map_path)
            connection.execute("CREATE TABLE filing_forms (filing_id TEXT PRIMARY KEY, form_type TEXT NOT NULL)")
            connection.executemany("INSERT INTO filing_forms VALUES (?, ?)", [("a", "8-K"), ("b", "10-Q")])
            connection.execute(
                "CREATE TABLE source_authority (cik TEXT, accession_number TEXT, document_id TEXT, "
                "content_format TEXT, source_version_key TEXT, source_revision_rank INTEGER, "
                "partition_id INTEGER, filing_id TEXT)"
            )
            connection.execute(
                "INSERT INTO source_authority VALUES ('1', 'acc', 'doc', 'html', 'version', 7, 202607, 'a')"
            )
            connection.commit()
            connection.close()
            self.assertEqual(load_filing_forms(map_path, {"a", "b"}), {"a": "8-K", "b": "10-Q"})
            self.assertEqual(
                load_partition_authority(map_path, 202607),
                {("1", "acc", "doc", "html"): ("version", 7, "a")},
            )

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
        filing = FilingWatermark(5, 5, "2026-07-16 00:00:00.000", 789)
        partitions = [{"partition_id": 202607, "source_rows": 10, "source_chars": 90}]
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            load_or_create_run_manifest(root, args, "test", [], original, filing, partitions)
            with self.assertRaisesRegex(RuntimeError, "source changed since run started"):
                load_or_create_run_manifest(root, args, "test", [], changed, filing, partitions)


if __name__ == "__main__":
    unittest.main()
