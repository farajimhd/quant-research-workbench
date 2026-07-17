from __future__ import annotations

import concurrent.futures
import unittest
import sqlite3
import threading
import time
from datetime import UTC, date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import ANY, patch

import pyarrow as pa
import pyarrow.parquet as pq

from pipelines.sec.edgar.sec_filing_text_rendered_v3_rebuild import (
    SourceWatermark,
    SOURCE_COLUMNS,
    FilingWatermark,
    PartitionResult,
    build_row_group_bundles,
    build_rendered_row,
    collect_partition_results,
    clickhouse_insert_slot,
    export_source_partition,
    load_or_create_run_manifest,
    load_filing_forms,
    load_partition_authority,
    prepare_lookup_database,
    prepare_partition_export,
    process_row_group_bundle,
    initialize_rebuild_worker,
    rebuild_stop_path,
    request_rebuild_stop,
    reset_invalidated_partition,
    run_jobs,
    staging_table_for_run,
    validate_completed_bundle_prefix,
)
from pipelines.sec.edgar.sec_pipeline.text_renderer import SEC_PACKED_TEXT_RENDERER_VERSION


class SecRenderedV3RebuildTest(unittest.TestCase):
    def test_row_group_bundles_are_bounded_and_cover_every_group_once(self) -> None:
        bundles = build_row_group_bundles(19, 8)

        self.assertEqual(bundles, [(1, 0, 8), (2, 8, 16), (3, 16, 19)])
        covered = [group for _, start, end in bundles for group in range(start, end)]
        self.assertEqual(covered, list(range(19)))

    def test_completed_bundle_checkpoints_must_be_a_contiguous_prefix(self) -> None:
        validate_completed_bundle_prefix({1, 2, 3}, total_bundles=3)
        with self.assertRaisesRegex(RuntimeError, "not a contiguous prefix"):
            validate_completed_bundle_prefix({1, 3}, total_bundles=3)

    def test_stop_request_is_atomic_and_contains_failure_identity(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            request_rebuild_stop(root, 202001, RuntimeError("bad table"))

            payload = rebuild_stop_path(root).read_text(encoding="utf-8")
            self.assertIn('"partition_id": 202001', payload)
            self.assertIn("RuntimeError: bad table", payload)
            self.assertEqual(list(root.glob("*.tmp")), [])

    def test_invalidated_export_resets_staging_and_bundle_checkpoints_once(self) -> None:
        class RecordingClient:
            def __init__(self) -> None:
                self.sql: list[str] = []

            def execute(self, sql: str) -> str:
                self.sql.append(sql)
                return ""

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            marker = root / "source_export_requires_bundle_reset"
            marker.write_text("reset", encoding="utf-8")
            client = RecordingClient()
            job = SimpleNamespace(
                database="q_live",
                staging_table="stage_v3",
                bundle_manifest_table="bundle_manifest_v3",
                run_id="run",
                partition_id=202001,
            )
            with patch("pipelines.sec.edgar.sec_filing_text_rendered_v3_rebuild.cleanup_partition_staging") as cleanup:
                reset_invalidated_partition(client, job, root)

            cleanup.assert_called_once_with(client, job)
            self.assertIn("ALTER TABLE `q_live`.`bundle_manifest_v3` DELETE", client.sql[0])
            self.assertFalse(marker.exists())

    def test_successful_bundle_read_initializes_corruption_state(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "sec_filing_text_rendered_v3_rebuild" / "run" / "partitions" / "202001"
            root.mkdir(parents=True)
            source_path = root / "source_202001.parquet"
            pq.write_table(pa.table({column: pa.array([], type=pa.string()) for column in SOURCE_COLUMNS}), source_path)
            job = SimpleNamespace(
                staging_table="stage_v3",
                parquet_row_group_bytes=1024,
                parquet_file_bytes=2048,
                run_id="run",
                partition_id=202001,
                file_root_win=str(Path(temp_dir)),
                file_root_ch="/mnt/test",
                insert_threads=1,
                max_memory_usage=1024,
                keep_temp_files=True,
            )

            result = process_row_group_bundle(
                object(), job, source_path, {}, {}, set(), 1, 0, 0
            )

            self.assertEqual(result.status, "ok")
            self.assertEqual(result.source_rows, 0)

    def test_source_export_checks_parquet_pages_after_each_large_text_row(self) -> None:
        class RecordingClient:
            def __init__(self) -> None:
                self.sql = ""

            def execute(self, sql: str) -> str:
                self.sql = sql
                return ""

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            client = RecordingClient()
            job = SimpleNamespace(
                file_root_win=str(root),
                file_root_ch="/mnt/test",
                max_rows_per_partition=0,
                database="q_live",
                source_table="sec_filing_text_v3",
                partition_id=202002,
                export_threads=2,
                max_memory_usage=32 * 1024**3,
            )
            export_source_partition(client, job, root / "source.parquet")

            self.assertIn("output_format_parquet_batch_size=1", client.sql)
            self.assertIn("output_format_parquet_row_group_size_bytes=268435456", client.sql)
            self.assertIn("output_format_parquet_parallel_encoding=0", client.sql)
            self.assertIn("output_format_parquet_write_bloom_filter=0", client.sql)

    def test_worker_returned_error_updates_partition_manifest(self) -> None:
        future: concurrent.futures.Future[PartitionResult] = concurrent.futures.Future()
        failure = PartitionResult(202001, 0, 0, 0, 0, 0, 0, 1.0, "error", "bundle failed")
        future.set_result(failure)
        job = SimpleNamespace(partition_id=202001)
        futures = {future: job}
        results: list[PartitionResult] = []

        with patch(
            "pipelines.sec.edgar.sec_filing_text_rendered_v3_rebuild.insert_partition_manifest"
        ) as insert_manifest:
            first_failure = collect_partition_results(
                object(), futures, [future], results, 0, 0, 1, 0.0
            )

        self.assertEqual(first_failure, failure)
        insert_manifest.assert_called_once_with(ANY, job, failure)

    def test_insert_gate_limits_database_concurrency_without_limiting_workers(self) -> None:
        initialize_rebuild_worker(threading.BoundedSemaphore(2))
        state = {"active": 0, "maximum": 0}
        lock = threading.Lock()

        def insert_task() -> None:
            with clickhouse_insert_slot():
                with lock:
                    state["active"] += 1
                    state["maximum"] = max(state["maximum"], state["active"])
                time.sleep(0.02)
                with lock:
                    state["active"] -= 1

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(lambda _: insert_task(), range(8)))

        self.assertEqual(state["maximum"], 2)

    def test_scheduler_stops_exporting_after_first_worker_failure(self) -> None:
        with TemporaryDirectory() as temp_dir:
            jobs = [SimpleNamespace(partition_id=value, run_root=temp_dir) for value in range(1, 6)]
            prepared: list[int] = []

            def prepare(_client: object, job: SimpleNamespace) -> None:
                prepared.append(job.partition_id)

            def process(job: SimpleNamespace) -> PartitionResult:
                if job.partition_id == 1:
                    raise RuntimeError("expected failure")
                return PartitionResult(job.partition_id, 1, 1, 0, 1, 1, 1, 0.01, "ok")

            with (
                patch(
                    "pipelines.sec.edgar.sec_filing_text_rendered_v3_rebuild.concurrent.futures.ProcessPoolExecutor",
                    concurrent.futures.ThreadPoolExecutor,
                ),
                patch(
                    "pipelines.sec.edgar.sec_filing_text_rendered_v3_rebuild.prepare_partition_export",
                    side_effect=prepare,
                ),
                patch(
                    "pipelines.sec.edgar.sec_filing_text_rendered_v3_rebuild.process_exported_partition",
                    side_effect=process,
                ),
                patch(
                    "pipelines.sec.edgar.sec_filing_text_rendered_v3_rebuild.insert_partition_manifest"
                ) as manifest,
            ):
                with self.assertRaisesRegex(RuntimeError, "partition 1 failed"):
                    run_jobs(object(), jobs, max_workers=2, total_partitions=5, already_completed=0)

            self.assertIn(1, prepared)
            self.assertLessEqual(len(prepared), 2)
            self.assertNotIn(3, prepared)
            self.assertTrue(manifest.called)
            self.assertTrue((Path(temp_dir) / "partition_results.json").exists())

    def test_completed_legacy_partition_export_is_validated_adopted_and_reused(self) -> None:
        with TemporaryDirectory() as temp_dir:
            run_root = Path(temp_dir) / "sec_filing_text_rendered_v3_rebuild" / "run"
            partition_root = run_root / "partitions" / "202607"
            partition_root.mkdir(parents=True)
            source_path = partition_root / "source_202607.parquet"
            columns = {name: [""] for name in SOURCE_COLUMNS}
            columns["source_revision_rank"] = [1]
            pq.write_table(pa.table(columns), source_path)
            job = SimpleNamespace(
                run_id="run",
                run_root=str(run_root),
                database="q_live",
                source_table="sec_filing_text_v3",
                staging_table="stage_v3",
                partition_id=202607,
                expected_rows=1,
                expected_source_chars=0,
            )

            with (
                patch("pipelines.sec.edgar.sec_filing_text_rendered_v3_rebuild.cleanup_partition_staging"),
                patch("pipelines.sec.edgar.sec_filing_text_rendered_v3_rebuild.export_source_partition") as export,
            ):
                reused = prepare_partition_export(object(), job)

            self.assertTrue(reused)
            self.assertFalse(export.called)
            self.assertTrue((partition_root / "source_export.json").exists())

    def test_mismatched_partition_export_receipt_forces_reexport(self) -> None:
        with TemporaryDirectory() as temp_dir:
            run_root = Path(temp_dir) / "sec_filing_text_rendered_v3_rebuild" / "run"
            partition_root = run_root / "partitions" / "202607"
            partition_root.mkdir(parents=True)
            source_path = partition_root / "source_202607.parquet"
            columns = {name: [""] for name in SOURCE_COLUMNS}
            columns["source_revision_rank"] = [1]
            pq.write_table(pa.table(columns), source_path)
            (partition_root / "source_export.json").write_text('{"run_id":"wrong"}', encoding="utf-8")
            job = SimpleNamespace(
                run_id="run",
                run_root=str(run_root),
                database="q_live",
                source_table="sec_filing_text_v3",
                staging_table="stage_v3",
                partition_id=202607,
                expected_rows=1,
                expected_source_chars=0,
            )

            def export(_client: object, _job: object, path: Path) -> None:
                pq.write_table(pa.table(columns), path)

            with (
                patch("pipelines.sec.edgar.sec_filing_text_rendered_v3_rebuild.cleanup_partition_staging"),
                patch(
                    "pipelines.sec.edgar.sec_filing_text_rendered_v3_rebuild.export_source_partition",
                    side_effect=export,
                ) as export_mock,
            ):
                reused = prepare_partition_export(object(), job)

            self.assertFalse(reused)
            self.assertTrue(export_mock.called)
            self.assertIn('"run_id": "run"', (partition_root / "source_export.json").read_text(encoding="utf-8"))

    def test_completed_temporary_lookup_is_promoted_on_resume(self) -> None:
        source = SourceWatermark(1, 100, 7, "2026-07-16 00:00:00.000", 123)
        filing = FilingWatermark(1, 1, "2026-07-16 00:00:00.000", 456)
        args = SimpleNamespace(file_root_win="D:/market-data", file_root_ch="/mnt/d/market-data")
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            temporary_path = root / "render_lookup.sqlite.tmp"
            connection = sqlite3.connect(temporary_path)
            connection.execute("CREATE TABLE filing_forms (filing_id TEXT, form_type TEXT)")
            connection.execute("INSERT INTO filing_forms VALUES ('filing', '8-K')")
            connection.execute(
                "CREATE TABLE source_authority (cik TEXT, accession_number TEXT, document_id TEXT, "
                "content_format TEXT, source_version_key TEXT, source_revision_rank INTEGER, "
                "partition_id INTEGER, filing_id TEXT)"
            )
            connection.execute(
                "INSERT INTO source_authority VALUES ('1', 'acc', 'doc', 'html', 'version', 7, 202607, 'filing')"
            )
            connection.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            connection.executemany(
                "INSERT INTO metadata VALUES (?, ?)",
                [
                    ("source_rows", "1"),
                    ("source_bytes", "100"),
                    ("source_max_revision_rank", "7"),
                    ("source_max_inserted_at", "2026-07-16 00:00:00.000"),
                    ("source_metadata_hash", "123"),
                    ("filing_rows", "1"),
                    ("unique_filing_ids", "1"),
                    ("filing_max_inserted_at", "2026-07-16 00:00:00.000"),
                    ("filing_metadata_hash", "456"),
                ],
            )
            connection.commit()
            connection.close()
            (root / "filing_form_map.parquet").touch()
            (root / "source_authority.parquet").touch()

            database_path = prepare_lookup_database(None, args, root, source, filing)

            self.assertEqual(database_path, root / "render_lookup.sqlite")
            self.assertTrue(database_path.exists())
            self.assertFalse(temporary_path.exists())
            self.assertFalse((root / "filing_form_map.parquet").exists())
            self.assertFalse((root / "source_authority.parquet").exists())

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
            row_groups_per_bundle=8,
            max_concurrent_inserts=1,
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
