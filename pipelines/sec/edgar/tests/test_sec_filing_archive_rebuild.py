from __future__ import annotations

import io
import tempfile
import tarfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from pipelines.sec.edgar import sec_filing_archive_rebuild as rebuild
from pipelines.sec.edgar import sec_filing_text_clickhouse_file_ingest as ingest
from pipelines.sec.edgar import sec_filing_text_extract_parts as extractor
from pipelines.sec.edgar import sec_historical_gap_fill as historical


class SecFilingArchiveRebuildTests(unittest.TestCase):
    def test_sec_file_ingest_disables_parallel_json_parsing_by_default(self) -> None:
        settings = ingest.settings_sql(SimpleNamespace(max_threads=4, max_memory_usage="16G"))

        self.assertIn("input_format_parallel_parsing = 0", settings)
        self.assertIn("max_block_size = 16", settings)

    def test_sec_file_ingest_can_explicitly_enable_parallel_json_parsing(self) -> None:
        settings = ingest.settings_sql(
            SimpleNamespace(max_threads=4, max_memory_usage="16G", input_format_parallel_parsing=True)
        )

        self.assertIn("input_format_parallel_parsing = 1", settings)

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

    def test_gzip_parts_use_explicit_clickhouse_compression(self) -> None:
        part = ingest.PartFile(
            run_id="run",
            dataset_name="text_source",
            target_table="sec_filing_text_v3",
            part_index=1,
            windows_path=Path("D:/market-data/part.jsonl.gz"),
            clickhouse_path="/mnt/d/market-data/part.jsonl.gz",
            expected_rows=1,
            expected_bytes=1,
            columns=["document_id"],
            structure="document_id String",
        )

        sql = ingest.file_table_function(part)

        self.assertIn("'gzip'", sql)
        self.assertIn("JSONEachRow", sql)

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

    def test_historical_archive_stage_disables_parallel_json_parsing(self) -> None:
        with mock.patch.object(historical.sys, "argv", ["sec_historical_gap_fill.py"]):
            args = historical.parse_args()

        stage = next(command for command in historical.build_commands(args, Path("logs")) if command.stage == "archive-text-rebuild")

        self.assertIn("--no-input-format-parallel-parsing", stage.command)
        self.assertEqual(stage.command[stage.command.index("--input-max-block-rows") + 1], "16")
        self.assertEqual(stage.command[stage.command.index("--text-insert-concurrency") + 1], "2")

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
            "text_insert_semaphore": object(),
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

    def test_archive_worker_writes_complete_gzip_parts(self) -> None:
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
                "compress_parts": True,
            }
            with mock.patch.object(extractor, "ClickHouseHttpClient", FakeClickHouseClient):
                result = extractor.process_archive_worker(payload)

            source_part = next(item for item in result["part_files"] if item["dataset_name"] == "text_source")
            source_path = Path(source_part["path"])
            self.assertEqual(result["status"], "ok")
            self.assertTrue(source_path.name.endswith(".jsonl.gz"))
            self.assertGreater(source_part["rows"], 0)
            with extractor.gzip.open(source_path, "rt", encoding="utf-8") as handle:
                row = handle.readline()
            self.assertIn("Complete submitted text.", row)

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
