from __future__ import annotations

import gzip
import io
import json
import sys
import tempfile
import tarfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pyarrow as pa
import pyarrow.parquet as pq

from pipelines.sec.edgar import sec_filing_archive_rebuild as rebuild
from pipelines.sec.edgar import sec_filing_text_clickhouse_file_ingest as ingest
from pipelines.sec.edgar import sec_filing_text_extract_parts as extractor
from pipelines.sec.edgar import sec_historical_gap_fill as historical
from pipelines.sec.edgar import sec_missing_document_repair as missing_document_repair
from pipelines.sec.edgar import sec_text_v3_schema as text_schema
from pipelines.sec.edgar.sec_pipeline import clickhouse_writer
from pipelines.sec.edgar.sec_parquet_parts import (
    ParquetShardWriter,
    arrow_type_for_column,
    validate_parquet_part,
)


class SecFilingArchiveRebuildTests(unittest.TestCase):
    def test_source_text_schema_uses_canonical_monthly_archive_partition(self) -> None:
        raw_sql = text_schema.DEFAULT_SCHEMA_PATH.read_text(encoding="utf-8")
        rendered = text_schema.render_schema(raw_sql, "q_live", "live_market_ssd", False)
        source_statement = next(
            statement
            for statement in text_schema.split_sql_statements(rendered)
            if "q_live.sec_filing_text_v3" in statement
        )

        self.assertIn("PARTITION BY toYYYYMM(source_archive_date)", source_statement)
        self.assertIn("ReplacingMergeTree(source_revision_rank)", source_statement)
        self.assertIn("ORDER BY (cik, accession_number, document_id, content_format)", source_statement)

    def test_archive_inventory_current_view_preserves_each_source_kind(self) -> None:
        raw_sql = text_schema.DEFAULT_SCHEMA_PATH.read_text(encoding="utf-8")
        rendered = text_schema.render_schema(raw_sql, "q_live", "live_market_ssd", False)
        view_statement = next(
            statement for statement in text_schema.split_sql_statements(rendered)
            if "sec_filing_archive_accession_current_v3" in statement
        )

        self.assertIn("GROUP BY accession_number, source_kind", view_statement)
        self.assertIn("USING (accession_number, source_kind, source_version_key)", view_statement)
        self.assertIn("FROM q_live.sec_filing_archive_accession_v3 AS a FINAL", view_statement)
        self.assertNotIn("FINAL AS", view_statement)

    def test_finalize_execute_is_propagated_by_explicit_stage_contract(self) -> None:
        with mock.patch.object(sys, "argv", ["sec_historical_gap_fill.py", "--finalize-only", "--execute"]):
            args = historical.parse_args()
        commands = {
            command.stage: command.command
            for command in historical.build_commands(args, Path("C:/tmp/sec-finalizer-tests"))
            if command.stage in historical.FINALIZE_ONLY_STAGES
        }

        gated_stages = {
            "filing-entity-backfill", "missing-document-repair", "filing-parent-reconcile",
            "acceptance-submissions-enrichment", "acceptance-raw-metadata-repair",
            "acceptance-archive-repair", "archive-identity-repair", "sec-bridge-rebuild",
        }
        for stage in gated_stages:
            self.assertIn("--execute", commands[stage], stage)
        for stage in {"archive-identity-audit", "sec-context-build", "integrity-audit"}:
            self.assertNotIn("--execute", commands[stage], stage)

    def test_part_checkpoints_only_apply_to_current_table_generation(self) -> None:
        class FakeClient:
            def execute(self, _sql: str) -> str:
                return "\n".join(
                    [
                        json.dumps(
                            {
                                "run_id": "old",
                                "dataset_name": "text_source",
                                "part_index": 1,
                                "target_table": "sec_filing_text_v3",
                                "target_table_uuid": "old-uuid",
                                "part_path": "sec_filing_text_v3_part_20260701_1.parquet",
                                "status": "ok",
                                "expected_rows": 10,
                            }
                        ),
                        json.dumps(
                            {
                                "run_id": "current",
                                "dataset_name": "text_source",
                                "part_index": 2,
                                "target_table": "sec_filing_text_v3",
                                "target_table_uuid": "current-uuid",
                                "part_path": "sec_filing_text_v3_part_20260702_2.parquet",
                                "status": "ok",
                                "expected_rows": 20,
                            }
                        ),
                    ]
                )

        args = SimpleNamespace(
            database="q_live",
            part_manifest_table="sec_filing_text_file_ingest_manifest_v3",
            target_table_uuids={"sec_filing_text_v3": "current-uuid"},
        )

        records = ingest.load_latest_part_records(FakeClient(), args)

        self.assertEqual([(record.run_id, record.target_table_uuid) for record in records], [("current", "current-uuid")])

    def test_archive_rebuild_creates_missing_source_text_table_from_shared_schema(self) -> None:
        target_tables = set(ingest.EXPECTED_TARGET_TABLES.values())

        class FakeClient:
            def __init__(self) -> None:
                self.existing = target_tables - {"sec_filing_text_v3"}
                self.statements: list[str] = []

            def execute(self, sql: str) -> str:
                self.statements.append(sql)
                if sql.startswith("SELECT count() FROM system.tables"):
                    table = next(name for name in target_tables if f"name='{name}'" in sql)
                    return "1" if table in self.existing else "0"
                if sql.lstrip().startswith("CREATE TABLE IF NOT EXISTS q_live.sec_filing_text_v3"):
                    self.existing.add("sec_filing_text_v3")
                    return ""
                if sql.startswith("SELECT partition_key, sorting_key"):
                    return "toYYYYMM(source_archive_date)\tcik, accession_number, document_id, content_format"
                if sql.startswith("SELECT name, toString(uuid)"):
                    return "\n".join(f"{name}\tuuid-{name}" for name in sorted(self.existing))
                return ""

        client = FakeClient()

        created, table_uuids = rebuild.ensure_target_tables(client, "q_live", "live_market_ssd")

        self.assertEqual(created, {"sec_filing_text_v3"})
        self.assertEqual(table_uuids["sec_filing_text_v3"], "uuid-sec_filing_text_v3")
        source_ddl = next(sql for sql in client.statements if "CREATE TABLE IF NOT EXISTS q_live.sec_filing_text_v3" in sql)
        self.assertIn("PARTITION BY toYYYYMM(source_archive_date)", source_ddl)
        self.assertIn("ReplacingMergeTree(source_revision_rank)", source_ddl)

    def test_live_writer_uses_same_source_text_layout_as_historical_schema(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.sql = ""

            def execute(self, sql: str) -> str:
                self.sql = sql
                return ""

        client = FakeClient()
        with mock.patch.object(clickhouse_writer, "infer_storage_policy", return_value="live_market_ssd"):
            clickhouse_writer.create_text_source_table_schema(
                client,
                target_database="q_live",
                reference_database="q_live",
            )

        self.assertIn("PARTITION BY toYYYYMM(source_archive_date)", client.sql)
        self.assertIn("ORDER BY (cik, accession_number, document_id, content_format)", client.sql)

    def test_archive_rebuild_rejects_stale_hash_partitioned_source_table(self) -> None:
        class FakeClient:
            def execute(self, _sql: str) -> str:
                return "cityHash64(cik) % 64\tcik, accession_number, document_id, content_format"

        with self.assertRaisesRegex(RuntimeError, "Drop the stale source-text table"):
            rebuild.validate_source_text_layout(FakeClient(), "q_live")

    def test_archive_stage_never_uses_coarse_coverage_for_resume(self) -> None:
        command = historical.StageCommand(
            "archive-text-rebuild",
            ["python", "worker.py"],
            Path("worker.log"),
            True,
            ("sec_stage_archive_text_rebuild",),
        )

        self.assertFalse(historical.stage_already_completed(SimpleNamespace(), command))

    def test_targeted_repair_stages_never_use_coarse_coverage_for_resume(self) -> None:
        for stage in ("filing-entity-backfill", "missing-document-repair", "acceptance-archive-repair"):
            command = historical.StageCommand(stage, ["python", "worker.py"], Path("worker.log"), True)
            self.assertFalse(historical.stage_already_completed(SimpleNamespace(), command), stage)

    def test_sec_file_ingest_uses_parallel_native_parquet_reader(self) -> None:
        settings = ingest.settings_sql(SimpleNamespace(max_threads=4, max_memory_usage="16G"))

        self.assertIn("input_format_parquet_use_native_reader_v3 = 1", settings)
        self.assertIn("input_format_parquet_enable_row_group_prefetch = 1", settings)
        self.assertIn("input_format_parquet_verify_checksums = 1", settings)
        self.assertNotIn("max_block_size", settings)

    def test_partition_tasks_assigns_balanced_fixed_lanes(self) -> None:
        tasks = [{"archive_date": f"day-{index}"} for index in range(300)]

        lanes = rebuild.partition_tasks(tasks, 15)

        self.assertEqual(len(lanes), 15)
        self.assertEqual({len(lane) for lane in lanes}, {20})
        self.assertEqual(sum(len(lane) for lane in lanes), 300)

    def test_worker_count_never_exceeds_windows_process_limit(self) -> None:
        count = rebuild.bounded_worker_count(96, 300)

        if rebuild.os.name == "nt":
            self.assertEqual(count, 61)
        else:
            self.assertEqual(count, 96)

    def test_archive_rebuild_default_is_32_workers(self) -> None:
        argv = ["sec_filing_archive_rebuild.py", "--start-date", "2026-07-01", "--end-date", "2026-07-02"]
        with mock.patch.dict(rebuild.os.environ, {}, clear=True), mock.patch.object(rebuild.sys, "argv", argv):
            args = rebuild.parse_args()

        self.assertEqual(args.workers, 32)
        self.assertEqual(args.insert_concurrency, 8)
        self.assertEqual(args.insert_max_threads, 8)

    def test_parquet_parts_use_native_file_format(self) -> None:
        part = ingest.PartFile(
            run_id="run",
            dataset_name="text_source",
            target_table="sec_filing_text_v3",
            part_index=1,
            windows_path=Path("D:/market-data/part.parquet"),
            clickhouse_path="/mnt/d/market-data/part.parquet",
            expected_rows=1,
            expected_bytes=1,
            columns=["document_id"],
            structure="document_id String",
        )

        sql = ingest.file_table_function(part)

        self.assertIn("'Parquet'", sql)
        self.assertNotIn("JSONEachRow", sql)

    def test_parquet_writer_shards_by_bytes_without_splitting_text_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            writer = ParquetShardWriter(
                dataset_name="text_source",
                target_table="sec_filing_text_v3",
                output_directory=Path(temp_dir),
                filename_prefix="sample",
                columns=["document_id", "source_text", "source_text_byte_count"],
                archive_index=1,
                row_group_bytes=100,
                file_bytes=220,
            )
            expected = ["A" * 180, "B" * 180, "C" * 180]
            for index, text in enumerate(expected):
                writer.append(
                    {
                        "document_id": str(index),
                        "source_text": text,
                        "source_text_byte_count": len(text.encode("utf-8")),
                    }
                )
            parts = writer.close()

            self.assertEqual(len(parts), 3)
            actual = []
            for part in parts:
                metadata = validate_parquet_part(Path(part["path"]), part["rows"], part["columns"])
                self.assertEqual(metadata["rows"], 1)
                actual.extend(pq.read_table(part["path"], columns=["source_text"]).column("source_text").to_pylist())
            self.assertEqual(actual, expected)

    def test_parquet_writer_reasserts_output_directory_when_opening_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_directory = Path(temp_dir) / "shared-dataset"
            writer = ParquetShardWriter(
                dataset_name="document",
                target_table="sec_filing_document_v3",
                output_directory=output_directory,
                filename_prefix="sample",
                columns=["document_id"],
                archive_index=1,
                row_group_bytes=1024,
                file_bytes=1024,
            )
            output_directory.rmdir()
            writer.append({"document_id": "doc"})

            parts = writer.close()

            self.assertEqual(len(parts), 1)
            self.assertTrue(Path(parts[0]["path"]).exists())

    def test_worker_cleanup_keeps_shared_directory_until_pool_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            parts_root = Path(temp_dir) / "parts"
            dataset_directory = parts_root / "sec_filing_v3_parts"
            dataset_directory.mkdir(parents=True)
            part_path = dataset_directory / "worker-1.parquet"
            part_path.write_bytes(b"part")

            missing_document_repair.cleanup_parts({"part_files": [{"path": str(part_path)}]})

            self.assertFalse(part_path.exists())
            self.assertTrue(dataset_directory.exists())

            missing_document_repair.prune_empty_part_directories(parts_root)

            self.assertFalse(dataset_directory.exists())

    def test_parquet_schema_uses_canonical_sec_lineage_and_inventory_types(self) -> None:
        expected_types = {
            "source_section_ordinal": pa.uint16(),
            "document_count": pa.uint32(),
            "public_document_count": pa.uint32(),
            "source_revision_rank": pa.uint64(),
            "correction_order_key": pa.uint64(),
            "private_to_public": pa.uint8(),
            "filing_deleted": pa.uint8(),
            "document_deleted": pa.uint8(),
            "date_as_of_change": pa.date32(),
            "source_revision_at": pa.timestamp("ms", tz="UTC"),
            "entity_ciks": pa.list_(pa.string()),
        }

        for column, expected_type in expected_types.items():
            self.assertEqual(arrow_type_for_column(column), expected_type, column)

    def test_parquet_preflight_rejects_wrong_canonical_type_before_insert(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "wrong-lineage-type.parquet"
            table = pa.table(
                {
                    "accession_number": ["0001974158-24-000002"],
                    "source_revision_rank": ["1720000000000000000"],
                }
            )
            pq.write_table(table, path)

            with self.assertRaisesRegex(RuntimeError, "Parquet type mismatch.*source_revision_rank"):
                validate_parquet_part(path, 1, ["accession_number", "source_revision_rank"])

    def test_parquet_preflight_does_not_query_clickhouse(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            writer = ParquetShardWriter(
                dataset_name="document",
                target_table="sec_filing_document_v3",
                output_directory=Path(temp_dir),
                filename_prefix="sample",
                columns=["document_id"],
                archive_index=1,
                row_group_bytes=1024,
                file_bytes=1024,
            )
            writer.append({"document_id": "doc"})
            item = writer.close()[0]
            part = ingest.PartFile(
                run_id="run",
                dataset_name="document",
                target_table="sec_filing_document_v3",
                part_index=item["part_index"],
                windows_path=Path(item["path"]),
                clickhouse_path="/mnt/d/sample.parquet",
                expected_rows=1,
                expected_bytes=item["bytes"],
                columns=item["columns"],
                structure="",
                row_groups=item["row_groups"],
            )

            class NoQueryClient:
                def execute(self, _sql: str) -> str:
                    raise AssertionError("preflight must not query ClickHouse")

            ingest.preflight_parts(NoQueryClient(), SimpleNamespace(), [part])

    def test_legacy_recovery_streams_json_into_parquet(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "legacy.jsonl.gz"
            row = {column: "" for column in extractor.TEXT_SOURCE_COLUMNS}
            row.update(
                {
                    "document_id": "doc",
                    "sequence_number": 1,
                    "source_archive_date": "2026-07-01",
                    "source_text": "complete source text",
                    "source_text_char_count": 20,
                    "source_text_byte_count": 20,
                    "inserted_at": "2026-07-01 12:00:00.000",
                }
            )
            with gzip.open(source, "wt", encoding="utf-8") as handle:
                handle.write(json.dumps(row) + "\n")
            task = {
                "archive_date": "2026-07-01",
                "archive_path": str(root / "20260701.nc.tar.gz"),
                "archive_index": 1,
                "part_paths": {"text_source": str(source)},
            }
            payload = {
                "run_root": str(root / "run"),
                "parquet_row_group_bytes": 1024,
                "parquet_file_bytes": 2048,
                "parquet_compression_level": 1,
            }

            result = rebuild.recovery_result(task, payload)

            self.assertEqual(result["text_source_rows"], 1)
            self.assertEqual(result["part_files"][0]["format"], "Parquet")
            self.assertIn(str(source), result["cleanup_paths"])
            actual = pq.read_table(result["part_files"][0]["path"], columns=["source_text"])
            self.assertEqual(actual.column("source_text")[0].as_py(), "complete source text")

    def test_legacy_success_log_only_recovers_explicit_ok_archives(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            log = Path(temp_dir) / "run" / "logs" / "text-extract.log"
            log.parent.mkdir(parents=True)
            log.write_text(
                "source_run_id=sec_text_extract_20260712_120755\n"
                "completed=1 last=20190102.nc.tar.gz status=ok\n"
                "completed=2 last=20190103.nc.tar.gz status=failed\n",
                encoding="utf-8",
            )

            recovered = rebuild.legacy_successful_dates(Path(temp_dir))

        self.assertEqual(recovered["sec_text_extract_20260712_120755"], {"2019-01-02"})

    def test_failed_dataset_cleanup_is_date_scoped_synchronous_and_verified(self) -> None:
        records = [
            ingest.PartManifestRecord(
                run_id="legacy_run",
                dataset_name="text_source",
                target_table="sec_filing_text_v3",
                part_index=1,
                part_path="D:/parts/sec_filing_text_v3_part_20190213_000001.jsonl.gz",
                status="failed",
                expected_rows=17164,
            ),
            ingest.PartManifestRecord(
                run_id="legacy_run",
                dataset_name="document",
                target_table="sec_filing_document_v3",
                part_index=1,
                part_path="D:/parts/sec_filing_document_v3_part_20190213_000001.jsonl.gz",
                status="ok",
                expected_rows=18000,
            ),
        ]
        checkpoints = rebuild.build_dataset_checkpoints(records, {"2019-02-13"})
        retry_keys = rebuild.failed_dataset_keys(checkpoints, set())

        class CleanupClient:
            def __init__(self) -> None:
                self.sql: list[str] = []
                self.counts = iter((1155, 0))

            def execute(self, sql: str) -> str:
                self.sql.append(sql)
                if sql.startswith("SELECT count()"):
                    return str(next(self.counts))
                return ""

        client = CleanupClient()
        args = SimpleNamespace(database="q_live", cleanup_date_batch_size=500)
        summary = rebuild.cleanup_failed_dataset_rows(
            client,
            args,
            checkpoints,
            retry_keys,
            progress_layout="text",
        )

        self.assertEqual(retry_keys, {("legacy_run", "text_source", "2019-02-13")})
        self.assertEqual(summary, {"failed_dataset_attempts": 1, "rows_removed": 1155, "delete_batches": 1})
        delete_sql = next(sql for sql in client.sql if sql.startswith("DELETE FROM"))
        self.assertIn("`q_live`.`sec_filing_text_v3`", delete_sql)
        self.assertIn("source_run_id = 'legacy_run'", delete_sql)
        self.assertIn("toDate('2019-02-13')", delete_sql)
        self.assertIn("lightweight_deletes_sync = 2", delete_sql)

    def test_completed_archive_unit_blocks_stale_failed_part_cleanup(self) -> None:
        record = ingest.PartManifestRecord(
            run_id="legacy_run",
            dataset_name="text_source",
            target_table="sec_filing_text_v3",
            part_index=1,
            part_path="D:/parts/sec_filing_text_v3_part_20190213_000001.jsonl.gz",
            status="failed",
            expected_rows=10,
        )
        checkpoints = rebuild.build_dataset_checkpoints([record], {"2019-02-13"})

        retry_keys = rebuild.failed_dataset_keys(checkpoints, {("legacy_run", "2019-02-13")})

        self.assertEqual(retry_keys, set())

    def test_recovery_preserves_successful_dataset_and_converts_only_failed_dataset(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            document_source = root / "sec_filing_document_v3_part_20190213_000001.jsonl.gz"
            text_source = root / "sec_filing_text_v3_part_20190213_000001.jsonl.gz"
            with gzip.open(document_source, "wt", encoding="utf-8") as handle:
                handle.write("{}\n")
            source_row = {column: "" for column in extractor.TEXT_SOURCE_COLUMNS}
            source_row.update(
                {
                    "document_id": "doc",
                    "sequence_number": 1,
                    "source_archive_date": "2019-02-13",
                    "source_text": "complete source text",
                    "source_text_char_count": 20,
                    "source_text_byte_count": 20,
                    "inserted_at": "2019-02-13 12:00:00.000",
                }
            )
            with gzip.open(text_source, "wt", encoding="utf-8") as handle:
                handle.write(json.dumps(source_row) + "\n")
            records = [
                ingest.PartManifestRecord(
                    "legacy_run", "document", "sec_filing_document_v3", 1, str(document_source), "ok", 7
                ),
                ingest.PartManifestRecord(
                    "legacy_run", "text_source", "sec_filing_text_v3", 1, str(text_source), "failed", 1
                ),
            ]
            checkpoints = rebuild.build_dataset_checkpoints(records, {"2019-02-13"})
            retry_keys = rebuild.failed_dataset_keys(checkpoints, set())
            task = {
                "kind": "recovery",
                "source_run_id": "legacy_run",
                "archive_date": "2019-02-13",
                "archive_path": str(root / "20190213.nc.tar.gz"),
                "archive_index": 1,
                "part_paths": {"document": str(document_source), "text_source": str(text_source)},
            }
            rebuild.annotate_recovery_tasks([task], checkpoints, retry_keys)
            payload = {
                "run_root": str(root / "run"),
                "parquet_row_group_bytes": 1024,
                "parquet_file_bytes": 2048,
                "parquet_compression_level": 1,
            }

            result = rebuild.recovery_result(task, payload)

            self.assertEqual(task["completed_dataset_rows"], {"document": 7})
            self.assertEqual(result["document_rows"], 7)
            self.assertEqual(result["text_source_rows"], 1)
            self.assertEqual({item["dataset_name"] for item in result["part_files"]}, {"text_source"})
            self.assertIn(str(document_source), result["cleanup_paths"])
            self.assertIn(str(text_source), result["cleanup_paths"])
            ingest_args = SimpleNamespace(parts_root_win=str(root), parts_root_ch="/mnt/test")
            task["source_run_id"] = "legacy_run"
            parts, _ = rebuild.build_and_preflight_parts(object(), ingest_args, task, result)
            self.assertEqual(len(parts), 1)
            self.assertEqual(result["document_rows"], 7)
            self.assertEqual(result["text_source_rows"], 1)

    def test_legacy_state_without_part_indexes_uses_dataset_checkpoint(self) -> None:
        records = [
            ingest.PartManifestRecord(
                "legacy_run",
                "document",
                "sec_filing_document_v3",
                10,
                "D:/parts/sec_filing_document_v3_part_20190213_000010.jsonl.gz",
                "ok",
                7,
            ),
            ingest.PartManifestRecord(
                "legacy_run",
                "text_source",
                "sec_filing_text_v3",
                10,
                "D:/parts/sec_filing_text_v3_part_20190213_000010.jsonl.gz",
                "failed",
                5,
            ),
        ]
        checkpoints = rebuild.build_dataset_checkpoints(records, {"2019-02-13"})
        retry_keys = rebuild.failed_dataset_keys(checkpoints, set())
        task = {
            "source_run_id": "legacy_run",
            "archive_date": "2019-02-13",
            "recovery_part_files": [
                {"dataset_name": "document", "rows": 7, "format": "JSONEachRow", "path": "document.jsonl.gz"},
                {"dataset_name": "text_source", "rows": 5, "format": "JSONEachRow", "path": "source.jsonl.gz"},
            ],
        }

        rebuild.annotate_recovery_tasks([task], checkpoints, retry_keys)

        self.assertEqual(task["completed_dataset_rows"], {"document": 7})

    def test_dirty_dataset_forces_reinsert_of_previously_ok_shard(self) -> None:
        part = ingest.PartFile(
            run_id="legacy_run",
            dataset_name="text_source",
            target_table="sec_filing_text_v3",
            part_index=2019021301,
            windows_path=Path("D:/parts/source.parquet"),
            clickhouse_path="/mnt/d/parts/source.parquet",
            expected_rows=10,
            expected_bytes=100,
            columns=["document_id"],
            structure="document_id String",
        )
        status = {(part.run_id, part.dataset_name, part.part_index): "ok"}

        self.assertTrue(rebuild.should_skip_part(part, "2019-02-13", status, set()))
        self.assertFalse(
            rebuild.should_skip_part(
                part,
                "2019-02-13",
                status,
                {("legacy_run", "text_source", "2019-02-13")},
            )
        )

    def test_archive_progress_bar_is_stable_width(self) -> None:
        self.assertEqual(historical.archive_progress_bar(10, 20), "[######------] 10/20")
        self.assertEqual(historical.archive_progress_bar(0, 0), "[------------] 0/0")

    def test_historical_execute_enables_archive_transaction_stage(self) -> None:
        args = SimpleNamespace(
            force_download=False,
            allow_g_drive=False,
            limit_days=0,
            limit_archives=0,
            max_filings_per_archive=0,
            text_limit_parts=0,
            bulk_limit_ciks=0,
            execute=True,
        )

        command = historical.add_required_execute_flag(["python", "sec_filing_archive_rebuild.py"], args)

        self.assertEqual(command[-1], "--execute")

    def test_historical_archive_stage_uses_parallel_parquet_defaults(self) -> None:
        with mock.patch.object(historical.sys, "argv", ["sec_historical_gap_fill.py"]):
            args = historical.parse_args()

        stage = next(command for command in historical.build_commands(args, Path("logs")) if command.stage == "archive-text-rebuild")

        self.assertNotIn("--no-input-format-parallel-parsing", stage.command)
        self.assertEqual(stage.command[stage.command.index("--insert-concurrency") + 1], "8")
        self.assertEqual(stage.command[stage.command.index("--parquet-row-group-mb") + 1], "256")
        self.assertEqual(stage.command[stage.command.index("--parquet-file-mb") + 1], "1024")

    def test_archive_lane_stops_after_first_failure(self) -> None:
        class FakeEvent:
            def __init__(self) -> None:
                self.value = False

            def is_set(self) -> bool:
                return self.value

            def set(self) -> None:
                self.value = True

        class FakeQueue:
            def __init__(self) -> None:
                self.items: list[dict[str, object]] = []

            def put(self, item: dict[str, object]) -> None:
                self.items.append(item)

        tasks = [
            {
                "kind": "extract",
                "archive_key": f"key-{index}",
                "archive_date": f"2026-07-0{index}",
                "archive_path": f"D:/archives/2026070{index}.nc.tar.gz",
                "state_path": f"D:/states/{index}.json",
            }
            for index in (1, 2)
        ]
        stop_event = FakeEvent()
        event_queue = FakeQueue()
        payload = {
            "lane": 1,
            "tasks": tasks,
            "event_queue": event_queue,
            "stop_event": stop_event,
            "insert_semaphore": object(),
            "clickhouse_url": "http://localhost",
            "user": "default",
            "password": "",
            "database": "q_live",
            "part_manifest_table": "parts",
            "archive_manifest_table": "archives",
            "insert_max_threads": 1,
            "insert_max_memory_usage": "1G",
        }
        failed_result = {"status": "failed", "errors": [{"reason": "test failure"}], "part_files": []}

        with (
            mock.patch.object(rebuild, "ClickHouseHttpClient", return_value=object()),
            mock.patch.object(rebuild.file_ingest, "load_latest_part_status", return_value={}),
            mock.patch.object(rebuild, "extractor_payload", return_value={}),
            mock.patch.object(rebuild.extractor, "process_archive_worker", return_value=failed_result) as process_archive,
            mock.patch.object(rebuild, "insert_archive_manifest"),
        ):
            result = rebuild.process_lane(payload)

        self.assertTrue(stop_event.is_set())
        self.assertEqual(process_archive.call_count, 1)
        self.assertEqual(len(result["archives"]), 1)
        self.assertEqual(result["archives"][0]["status"], "failed")
        self.assertEqual(event_queue.items[-1]["stage"], "failed")

    def test_historical_progress_retains_first_archive_failure(self) -> None:
        progress = historical.HistoricalFillProgress("text", [], Path("run"))
        progress.current_stage = "archive-text-rebuild"

        progress.log_line(
            'SEC_ARCHIVE_EVENT={"kind":"lane","lane":7,"archive":"20260701.nc.tar.gz",'
            '"stage":"failed","error":"oversized row","status":"failed"}'
        )

        self.assertEqual(progress.archive_failure["archive"], "20260701.nc.tar.gz")
        self.assertEqual(progress.status_by_stage["archive-text-rebuild"], "stopping: failed")

    def test_historical_progress_tracks_failed_insert_cleanup(self) -> None:
        progress = historical.HistoricalFillProgress("text", [], Path("run"))
        progress.current_stage = "archive-text-rebuild"

        progress.log_line(
            'SEC_ARCHIVE_EVENT={"kind":"cleanup","stage":"done","attempts":374,"rows":1134938,"batches":1}'
        )

        self.assertEqual(progress.archive_cleanup["stage"], "done")
        self.assertEqual(progress.archive_cleanup["rows"], 1134938)

    def test_historical_failure_panel_renders_in_compact_terminal(self) -> None:
        from rich.console import Console

        class FakeLive:
            current: object | None = None

            def __init__(self, renderable: object, **_kwargs: object) -> None:
                FakeLive.current = renderable

            def __enter__(self) -> "FakeLive":
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def update(self, renderable: object) -> None:
                FakeLive.current = renderable

        command = historical.StageCommand("archive-text-rebuild", ["python", "worker.py"], Path("worker.log"), True)
        with mock.patch("rich.live.Live", FakeLive):
            with historical.HistoricalFillProgress("rich", [command], Path("run")) as progress:
                progress.current_stage = "archive-text-rebuild"
                progress.log_line(
                    'SEC_ARCHIVE_EVENT={"kind":"lane","lane":3,"archive":"20260701.nc.tar.gz",'
                    '"stage":"failed","error":"oversized row","status":"failed"}'
                )

        for width in (80, 180):
            console = Console(record=True, width=width, height=30, force_terminal=False, file=io.StringIO())
            console.print(FakeLive.current)
            rendered = console.export_text()
            self.assertIn("Archive Failure - Stopping", rendered)
            self.assertIn("oversized row", rendered)

    def test_archive_worker_writes_complete_parquet_parts(self) -> None:
        class FakeClickHouseClient:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                pass

            def execute(self, _sql: str) -> str:
                return ""

        filing = b"""<SEC-DOCUMENT>
<SEC-HEADER>
ACCESSION NUMBER: 0000000001-26-000001
CENTRAL INDEX KEY: 0000000001
COMPANY CONFORMED NAME: Example Corp
CONFORMED SUBMISSION TYPE: 8-K
FILED AS OF DATE: 20260701
ACCEPTANCE-DATETIME: 20260701120000
</SEC-HEADER>
<DOCUMENT>
<TYPE>8-K
<SEQUENCE>1
<FILENAME>example.htm
<DESCRIPTION>Primary filing
<TEXT><html><body><h1>Example filing</h1><p>Complete submitted text.</p></body></html></TEXT>
</DOCUMENT>
</SEC-DOCUMENT>"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            archive = root / "20260701.nc.tar.gz"
            member = root / "submission.nc"
            member.write_bytes(filing)
            with tarfile.open(archive, "w:gz") as handle:
                handle.add(member, arcname="submission.nc")
            payload = {
                "archive_path": str(archive),
                "archive_index": 1,
                "parts_root": str(root / "parts"),
                "source_run_id": "test_run",
                "database": "q_live",
                "clickhouse_url": "http://localhost",
                "user": "default",
                "password": "",
                "max_filings_per_archive": 0,
                "sample_limit": 1,
                "sample_text_chars": 100,
                "parent_window_days_before": 1,
                "parent_window_days_after": 2,
                "parquet_row_group_bytes": 256,
                "parquet_file_bytes": 512,
                "parquet_compression_level": 1,
            }
            with mock.patch.object(extractor, "ClickHouseHttpClient", FakeClickHouseClient):
                result = extractor.process_archive_worker(payload)

            source_part = next(item for item in result["part_files"] if item["dataset_name"] == "text_source")
            inventory_part = next(item for item in result["part_files"] if item["dataset_name"] == "archive_accession")
            source_path = Path(source_part["path"])
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["archive_accession_rows"], 1)
            self.assertEqual(inventory_part["rows"], 1)
            self.assertTrue(source_path.name.endswith(".parquet"))
            self.assertGreater(source_part["rows"], 0)
            row = pq.read_table(source_path, columns=["source_text"]).column("source_text")[0].as_py()
            self.assertIn("Complete submitted text.", row)
            self.assertEqual(source_part["format"], "Parquet")
            self.assertGreaterEqual(source_part["row_groups"], 1)

            class NoQueryClickHouseClient:
                def __init__(self, *_args: object, **_kwargs: object) -> None:
                    pass

                def execute(self, _sql: str) -> str:
                    raise AssertionError("targeted extraction must use its supplied parent rows")

            targeted_payload = {
                **payload,
                "parts_root": str(root / "targeted-parts"),
                "target_members": ["submission.nc"],
                "target_accessions": ["0000000001-26-000001"],
                "parent_rows": [{
                    "filing_id": "filing-id", "accession_number": "0000000001-26-000001",
                    "accession_number_compact": "000000000126000001", "cik": "0000000001",
                    "form_type": "8-K", "accepted_at_utc": "2026-07-01 16:00:00.000000000",
                    "primary_document": "example.htm", "primary_document_url": "",
                    "filing_detail_url": "",
                }],
            }
            with mock.patch.object(extractor, "ClickHouseHttpClient", NoQueryClickHouseClient):
                targeted = extractor.process_archive_worker(targeted_payload)

            self.assertEqual(targeted["status"], "ok")
            self.assertEqual(targeted["filing_parent_rows"], 0)
            self.assertEqual(targeted["document_rows"], 1)
            self.assertEqual(targeted["parent_resolution_mode"], "supplied_only")

            archive_only_payload = {
                **payload,
                "parts_root": str(root / "archive-only-parts"),
                "target_members": ["submission.nc"],
                "target_accessions": ["0000000001-26-000001"],
                "parent_resolution_mode": "supplied_only",
                "parent_rows": [],
            }
            with mock.patch.object(extractor, "ClickHouseHttpClient", NoQueryClickHouseClient):
                archive_only = extractor.process_archive_worker(archive_only_payload)

            self.assertEqual(archive_only["status"], "ok")
            self.assertEqual(archive_only["filing_parent_rows"], 1)
            self.assertEqual(archive_only["document_rows"], 1)
            self.assertEqual(archive_only["parent_rows_loaded"], 0)
            self.assertEqual(archive_only["parent_resolution_mode"], "supplied_only")

            class SetEvent:
                def is_set(self) -> bool:
                    return True

            cancelled_payload = {
                **payload,
                "parts_root": str(root / "cancelled-parts"),
                "stop_event": SetEvent(),
            }
            with mock.patch.object(extractor, "ClickHouseHttpClient", FakeClickHouseClient):
                cancelled = extractor.process_archive_worker(cancelled_payload)

            self.assertEqual(cancelled["status"], "cancelled")
            self.assertEqual(cancelled["document_rows"], 0)


if __name__ == "__main__":
    unittest.main()
