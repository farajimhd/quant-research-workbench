from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from pipelines.sec.edgar.sec_source_text_revision_engine import (
    ensure_source_revision_engine,
    revision_engine_matches,
)


class RecordingClient:
    def __init__(self) -> None:
        self.sql: list[str] = []
        self.migration_exists = False
        self.backup_exists = False
        self.attached = False

    def execute(self, sql: str) -> str:
        normalized = " ".join(sql.split())
        self.sql.append(normalized)
        if "SELECT engine_full,partition_key,sorting_key,storage_policy" in normalized:
            engine = (
                "ReplacingMergeTree(source_revision_rank)"
                if "sec_filing_text_v3_revision_engine_migration" in normalized
                else "ReplacingMergeTree(inserted_at)"
            )
            return json.dumps(
                {
                    "engine_full": engine,
                    "partition_key": "toYYYYMM(source_archive_date)",
                    "sorting_key": "cik, accession_number, document_id, content_format",
                    "storage_policy": "live_market_ssd",
                }
            ) + "\n"
        if "SELECT count() FROM system.tables" in normalized:
            if "sec_filing_text_v3_revision_engine_migration" in normalized:
                return "1" if self.migration_exists else "0"
            if "sec_filing_text_v3_inserted_at_engine_backup" in normalized:
                return "1" if self.backup_exists else "0"
            return "1"
        if "HAVING inserted_winner != authority_winner" in normalized:
            return json.dumps(
                {
                    "inserted_partition": 202208,
                    "authority_partition": 202210,
                    "documents": 67,
                    "filings": 62,
                }
            ) + "\n"
        if normalized.startswith("CREATE TABLE"):
            self.migration_exists = True
            return ""
        if "SELECT DISTINCT partition_id FROM system.parts" in normalized:
            return "202208\n"
        if normalized.startswith("SELECT count() AS rows"):
            is_migration = "sec_filing_text_v3_revision_engine_migration" in normalized
            if is_migration and not self.attached:
                return '{"rows":0,"source_bytes":0,"metadata_hash":0}\n'
            return '{"rows":10,"source_bytes":1000,"metadata_hash":123}\n'
        if "ATTACH PARTITION" in normalized:
            self.attached = True
            return ""
        if normalized.startswith("EXCHANGE TABLES"):
            return ""
        if normalized.startswith("RENAME TABLE"):
            self.migration_exists = False
            self.backup_exists = True
            return ""
        return ""


class SourceTextRevisionEngineTest(unittest.TestCase):
    def test_revision_engine_matching_is_whitespace_insensitive(self) -> None:
        self.assertTrue(
            revision_engine_matches({"engine_full": "ReplacingMergeTree( source_revision_rank )"})
        )
        self.assertFalse(revision_engine_matches({"engine_full": "ReplacingMergeTree(inserted_at)"}))

    def test_migration_attaches_and_validates_each_partition(self) -> None:
        client = RecordingClient()
        with TemporaryDirectory() as temporary:
            report_path = Path(temporary) / "source_engine_migration.json"
            affected = ensure_source_revision_engine(
                client,
                database="q_live",
                table_name="sec_filing_text_v3",
                report_path=report_path,
                run_id="renderer-run",
            )
            report = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertEqual(affected, {202208, 202210})
        self.assertEqual(report["migration_status"], "completed")
        self.assertFalse(report["renderer_reset_completed"])
        self.assertTrue(any("ATTACH PARTITION ID '202208'" in sql for sql in client.sql))
        self.assertTrue(any(sql.startswith("EXCHANGE TABLES") for sql in client.sql))
        self.assertTrue(client.backup_exists)


if __name__ == "__main__":
    unittest.main()
