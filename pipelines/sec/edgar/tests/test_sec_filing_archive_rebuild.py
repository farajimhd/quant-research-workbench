from __future__ import annotations

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


if __name__ == "__main__":
    unittest.main()
