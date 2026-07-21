from __future__ import annotations

import argparse
import unittest
from datetime import UTC, date, datetime

from pipelines.sec.edgar.sec_missing_render_repair import (
    LEGACY_EXCLUSION_REASON,
    SOURCE_EXPORT_COLUMNS,
    build_rendered_row,
    cleanup_stale_skip_rows,
    ensure_operational_tables,
    missing_ctes_sql,
    reconcile_document_rows,
    reconcile_live_manifests,
    validate_args,
)
from pipelines.sec.edgar.sec_pipeline.text_renderer import SEC_PACKED_TEXT_RENDERER_VERSION


def args(**overrides: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "database": "q_live",
        "source_table": "sec_filing_text_v3",
        "rendered_table": "sec_filing_text_rendered_v3",
        "document_table": "sec_filing_document_v3",
        "skip_table": "sec_filing_document_skip_v3",
        "live_manifest_table": "sec_filing_live_ingest_manifest_v3",
        "candidate_table": "sec_filing_text_render_candidate_v3",
        "repair_manifest_table": "sec_filing_text_render_repair_manifest_v3",
        "workers": 4,
        "work_buckets": 8,
        "max_concurrent_exports": 2,
        "max_concurrent_inserts": 1,
        "export_threads": 2,
        "insert_threads": 2,
        "parquet_row_group_mib": 128,
        "parquet_file_mib": 1024,
        "execute": False,
        "confirm_sec_gateway_stopped": False,
        "output_root_win": "D:/market-data/prepared/repair",
        "file_root_win": "D:/market-data",
        "max_memory_usage": "24G",
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class RecordingClient:
    def __init__(self, responses: list[str] | None = None) -> None:
        self.sql: list[str] = []
        self.responses = iter(responses or [])

    def execute(self, sql: str) -> str:
        self.sql.append(sql)
        return next(self.responses, "")


class SecMissingRenderRepairTest(unittest.TestCase):
    def test_discovery_is_metadata_only_and_uses_external_sort_join(self) -> None:
        sql = missing_ctes_sql(args())
        self.assertIn("source_text_char_count", sql)
        self.assertNotIn("source_text,", sql)
        self.assertIn("LEFT JOIN rendered_rows", sql)
        self.assertIn("WHERE r.document_id=''", sql)

    def test_execute_requires_explicit_gateway_stop_confirmation(self) -> None:
        with self.assertRaisesRegex(SystemExit, "confirm-sec-gateway-stopped"):
            validate_args(args(execute=True))
        validate_args(args(execute=True, confirm_sec_gateway_stopped=True))

    def test_source_export_carries_authoritative_character_count(self) -> None:
        self.assertIn("source_text", SOURCE_EXPORT_COLUMNS)
        self.assertIn("source_text_char_count", SOURCE_EXPORT_COLUMNS)

    def test_rendered_row_preserves_source_revision_lineage(self) -> None:
        source = {
            "document_id": "doc",
            "filing_id": "filing",
            "accession_number": "0000000001-26-000001",
            "accession_number_compact": "000000000126000001",
            "cik": "0000000001",
            "text_kind": "primary_document",
            "source_archive_date": date(2026, 7, 21),
            "source_archive_member": "member.nc",
            "source_version_key": "revision-key",
            "source_revision_at": datetime(2026, 7, 21, tzinfo=UTC),
            "source_revision_rank": 123,
            "source_revision_kind": "daily_archive",
            "pac_event_id": None,
        }
        row = build_rendered_row(source, "<root/value>: 7", ["xml_tag_paths_preserved"], "repair-run", datetime.now(UTC))
        self.assertEqual(row["source_version_key"], "revision-key")
        self.assertEqual(row["source_revision_rank"], 123)
        self.assertEqual(row["normalizer_version"], SEC_PACKED_TEXT_RENDERER_VERSION)
        self.assertEqual(row["text_char_count"], len(row["text"]))

    def test_operational_tables_inherit_source_storage_policy(self) -> None:
        client = RecordingClient()
        ensure_operational_tables(client, args(), "live_market_ssd")
        self.assertEqual(len(client.sql), 2)
        self.assertTrue(all("storage_policy = 'live_market_ssd'" in sql for sql in client.sql))
        self.assertIn("text_kind, content_format", client.sql[0])

    def test_document_reconciliation_mutates_exact_source_revision(self) -> None:
        client = RecordingClient()
        reconcile_document_rows(client, args(), "repair-run")
        sql = client.sql[0]
        self.assertIn("ALTER TABLE `q_live`.`sec_filing_document_v3` UPDATE", sql)
        self.assertIn("document_id, source_version_key", sql)
        self.assertNotIn("INSERT INTO", sql)
        self.assertNotIn("inserted_at=", sql)
        self.assertIn("mutations_sync=2", sql)

    def test_skip_cleanup_deletes_only_legacy_reason_and_revision(self) -> None:
        client = RecordingClient(["2\n", ""])
        deleted = cleanup_stale_skip_rows(client, args(), "repair-run")
        self.assertEqual(deleted, 2)
        self.assertEqual(len(client.sql), 2)
        delete_sql = client.sql[1]
        self.assertIn(f"skip_reason='{LEGACY_EXCLUSION_REASON}'", delete_sql)
        self.assertIn("document_id, source_version_key", delete_sql)
        self.assertIn("had_legacy_exclusion_skip=1", delete_sql)

    def test_live_manifest_moves_only_legacy_rows_from_skip_to_render(self) -> None:
        client = RecordingClient()
        import pipelines.sec.edgar.sec_missing_render_repair as repair

        original = repair.table_exists
        repair.table_exists = lambda *_args: True
        try:
            reconcile_live_manifests(client, args(), "repair-run")
        finally:
            repair.table_exists = original
        sql = client.sql[0]
        self.assertIn("had_legacy_exclusion_skip=1", sql)
        self.assertIn("USING (accession_number, source_version_key)", sql)
        self.assertIn("if(m.expected_skip_rows >= repaired.reclassified_rows", sql)
        self.assertNotIn("greatest(m.expected_skip_rows -", sql)


if __name__ == "__main__":
    unittest.main()
