from __future__ import annotations

import gzip
import io
import json
import tempfile
import tarfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pyarrow.parquet as pq

from pipelines.sec.edgar import sec_filing_archive_rebuild as rebuild
from pipelines.sec.edgar import sec_filing_text_clickhouse_file_ingest as ingest
from pipelines.sec.edgar import sec_filing_text_extract_parts as extractor
from pipelines.sec.edgar import sec_historical_gap_fill as historical
from pipelines.sec.edgar.sec_parquet_parts import ParquetShardWriter, validate_parquet_part


class SecFilingArchiveRebuildTests(unittest.TestCase):
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

        command = historical.add_execute_flag(["python", "sec_filing_archive_rebuild.py"], args)

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
                "min_text_chars": 1,
                "max_text_chars": 0,
                "parquet_row_group_bytes": 256,
                "parquet_file_bytes": 512,
                "parquet_compression_level": 1,
            }
            with mock.patch.object(extractor, "ClickHouseHttpClient", FakeClickHouseClient):
                result = extractor.process_archive_worker(payload)

            source_part = next(item for item in result["part_files"] if item["dataset_name"] == "text_source")
            source_path = Path(source_part["path"])
            self.assertEqual(result["status"], "ok")
            self.assertTrue(source_path.name.endswith(".parquet"))
            self.assertGreater(source_part["rows"], 0)
            row = pq.read_table(source_path, columns=["source_text"]).column("source_text")[0].as_py()
            self.assertIn("Complete submitted text.", row)
            self.assertEqual(source_part["format"], "Parquet")
            self.assertGreaterEqual(source_part["row_groups"], 1)

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
