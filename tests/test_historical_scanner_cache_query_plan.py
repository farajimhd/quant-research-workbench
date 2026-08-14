from __future__ import annotations

import unittest
from datetime import UTC, datetime

from src.backend.application_registry import QUERY_PLANS
from src.backend.query_plans.historical_scanner_cache_v1 import (
    QUERY_PLAN_ID,
    QUERY_PLAN_VERSION,
    SCANNER_QMD_TABLE,
    cached_qmd_rows_query,
    cached_scanner_rows_query,
    json_each_row_insert,
    qmd_snapshot_complete_queries,
    qmd_snapshot_table_schemas,
    snapshot_table_schema,
    technical_snapshot_table_schema,
)


class HistoricalScannerCacheQueryPlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.snapshot_at = datetime(2026, 8, 11, 15, 0, tzinfo=UTC)

    def test_plan_registers_all_materialized_cache_tables(self) -> None:
        plan = next(row for row in QUERY_PLANS if row.plan_id == QUERY_PLAN_ID)
        self.assertEqual(plan.version, QUERY_PLAN_VERSION)
        self.assertEqual(len(plan.source_paths), 5)
        self.assertIn(SCANNER_QMD_TABLE, plan.source_paths)

    def test_schemas_are_revision_keyed_replacing_merge_trees(self) -> None:
        schemas = (*snapshot_table_schema(), *qmd_snapshot_table_schemas(), technical_snapshot_table_schema())
        self.assertTrue(all("ReplacingMergeTree(materialized_at_utc)" in sql for sql in schemas if "CREATE TABLE" in sql))
        self.assertTrue(all("source_revision" in sql for sql in schemas if "CREATE TABLE" in sql))

    def test_qmd_completion_requires_meta_and_exact_union_row_count(self) -> None:
        meta, count = qmd_snapshot_complete_queries(
            snapshot_at=self.snapshot_at,
            source_revision="revision-7",
        )
        self.assertIn("SELECT complete, market_count, indicator_count, row_count", meta)
        self.assertIn("SELECT count() AS row_count", count)
        self.assertIn("'canvas_historical_qmd_snapshot_v4'", meta)
        self.assertIn("'revision-7'", count)

    def test_cache_reads_are_bounded_and_revision_exact(self) -> None:
        qmd = cached_qmd_rows_query(
            snapshot_at=self.snapshot_at,
            source_revision="revision-8",
        )
        scanner = cached_scanner_rows_query(
            snapshot_at=self.snapshot_at,
            lookback_minutes=30,
            source_revision="revision-8",
        )
        self.assertIn("LIMIT 20000", qmd)
        self.assertIn("LIMIT 20000", scanner)
        self.assertIn("source_revision = 'revision-8'", qmd)
        self.assertIn("market_json", qmd)
        self.assertIn("source_revision = 'revision-8'", scanner)

    def test_insert_builder_rejects_unregistered_tables(self) -> None:
        self.assertEqual(
            json_each_row_insert(SCANNER_QMD_TABLE),
            f"INSERT INTO {SCANNER_QMD_TABLE} FORMAT JSONEachRow",
        )
        with self.assertRaisesRegex(ValueError, "not registered"):
            json_each_row_insert("q_live.some_other_table")


if __name__ == "__main__":
    unittest.main()
