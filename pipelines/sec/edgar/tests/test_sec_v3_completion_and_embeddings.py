from __future__ import annotations

import unittest
import sys
import zipfile
from contextlib import redirect_stdout
from datetime import date
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from pipelines.market_sip.events import clickhouse_build_text_tokens as tokens
from pipelines.sec.edgar import sec_acceptance_raw_metadata_repair as acceptance_repair
from pipelines.sec.edgar import sec_bulk_clickhouse_ingest as bulk_ingest
from pipelines.sec.edgar import sec_bulk_snapshot_refresh as snapshot_refresh
from pipelines.sec.edgar import sec_historical_gap_fill as historical
from pipelines.sec.edgar.sec_pipeline import submissions


class SecV3CompletionAndEmbeddingTests(unittest.TestCase):
    def test_required_archive_date_uses_last_completed_weekday(self) -> None:
        self.assertEqual(
            historical.required_archive_through_date("2026-07-12", today_utc=date(2026, 7, 13)),
            date(2026, 7, 10),
        )
        self.assertEqual(
            historical.required_archive_through_date("2026-07-15", today_utc=date(2026, 7, 14)),
            date(2026, 7, 13),
        )

    def test_incremental_gap_fill_limits_archive_work_but_reconciles_full_context(self) -> None:
        with mock.patch.object(
            sys,
            "argv",
            ["sec_historical_gap_fill.py", "--start-date", "2026-07-10", "--end-date", "2026-07-11"],
        ):
            args = historical.parse_args()
        commands = {command.stage: command.command for command in historical.build_commands(args, Path("logs"))}

        self.assertIn("2026-07-10", commands["archive-text-rebuild"])
        context_start = commands["sec-context-build"].index("--start-date") + 1
        self.assertEqual(commands["sec-context-build"][context_start], "2019-01-01")
        self.assertIn("acceptance-raw-metadata-repair", commands)
        self.assertNotIn("sec_filing_v2", commands["acceptance-raw-metadata-repair"])

    def test_raw_acceptance_repair_uses_only_explicit_utc_source_values(self) -> None:
        cte = acceptance_repair.resolved_raw_cte_sql("sec_core", "sec_bulk_mirror_filing_v3")
        args = SimpleNamespace(
            target_database="q_live",
            target_table="sec_filing_v3",
            mirror_database="sec_core",
            mirror_table="sec_bulk_mirror_filing_v3",
            max_partitions_per_insert_block=1000,
            max_threads=32,
        )
        sql = acceptance_repair.insert_replacements_sql(args, cte, "repair-test")
        delete_sql = acceptance_repair.delete_replaced_fallbacks_sql(args)

        self.assertIn("endsWith(acceptance_datetime_raw, 'Z')", cte)
        self.assertIn("parseDateTime64BestEffortOrNull", cte)
        self.assertIn("sec_core_submissions_raw_z_repair", cte)
        self.assertNotIn("sec_filing_v2", cte)
        self.assertNotIn("UNION ALL", cte)
        self.assertIn("r.accepted_at_utc AS accepted_at_utc", sql)
        self.assertIn("r.acceptance_datetime_raw AS acceptance_datetime_raw", sql)
        self.assertIn("f.accepted_at_source IN", sql)
        self.assertIn("max_partitions_per_insert_block = 1000", sql)
        self.assertIn("max_threads = 32", sql)
        self.assertIn("ALTER TABLE `q_live`.`sec_filing_v3`", delete_sql)
        self.assertIn("accepted_at_source IN", delete_sql)
        self.assertIn("FROM `sec_core`.`sec_bulk_mirror_filing_v3` FINAL", delete_sql)
        self.assertIn("mutations_sync = 2", delete_sql)

    def test_bulk_submission_fragment_uses_top_level_arrays_without_blank_company_replacement(self) -> None:
        artifact = bulk_ingest.SourceArtifact(
            source_name="submissions",
            source_kind="submissions_bulk",
            source_url="https://www.sec.gov/submissions.zip",
            path=Path("submissions.zip"),
            source_file_id="source-id",
            byte_size=1,
            sha256="abc",
        )
        payload = {
            "accessionNumber": ["0001181431-10-016632"],
            "filingDate": ["2010-03-16"],
            "reportDate": ["2010-03-16"],
            "acceptanceDateTime": ["2010-03-16T18:43:23.000Z"],
            "form": ["4"],
            "primaryDocument": ["doc.xml"],
        }

        companies, file_refs, filings = bulk_ingest.submission_member_rows(
            payload,
            "0000005981",
            artifact,
            "2026-07-13 00:00:00.000000000",
            member_name="CIK0000005981-submissions-001.json",
        )

        self.assertEqual(companies, [])
        self.assertEqual(file_refs, [])
        self.assertEqual(len(filings), 1)
        self.assertEqual(filings[0]["accepted_at_utc"], "2010-03-16T18:43:23.000000000Z")
        self.assertEqual(filings[0]["accepted_at_source"], "submissions_bulk_fragment")
        self.assertEqual(filings[0]["source_kind"], "submissions_bulk_fragment")

    def test_only_submission_fragment_signatures_change_with_parser_version(self) -> None:
        artifact = bulk_ingest.SourceArtifact("submissions", "submissions_bulk", "url", Path("x.zip"), "id", 1, "sha")
        parent = zipfile.ZipInfo("CIK0000005981.json")
        fragment = zipfile.ZipInfo("CIK0000005981-submissions-001.json")
        for info in (parent, fragment):
            info.CRC = 1
            info.file_size = 2
            info.compress_size = 1
        with mock.patch.object(bulk_ingest, "SUBMISSIONS_FRAGMENT_PARSER_VERSION", "1"):
            parent_v1 = bulk_ingest.member_signature(artifact, parent)
            fragment_v1 = bulk_ingest.member_signature(artifact, fragment)
        with mock.patch.object(bulk_ingest, "SUBMISSIONS_FRAGMENT_PARSER_VERSION", "2"):
            parent_v2 = bulk_ingest.member_signature(artifact, parent)
            fragment_v2 = bulk_ingest.member_signature(artifact, fragment)

        self.assertEqual(parent_v1, parent_v2)
        self.assertNotEqual(fragment_v1, fragment_v2)

    def test_bulk_snapshot_uses_snapshot_manifest_and_preserves_xbrl_fact_identity(self) -> None:
        statements: list[str] = []
        client = SimpleNamespace(execute=statements.append)

        bulk_ingest.create_database_and_tables(client, "sec_core", "")

        ddl = "\n".join(statements)
        self.assertIn("sec_bulk_mirror_snapshot_manifest_v3", ddl)
        self.assertNotIn("sec_bulk_mirror_member_manifest_v3", ddl)
        self.assertIn("ifNull(accession_number, ''), fact_id", ddl)

    def test_bulk_snapshot_header_has_no_legacy_member_manifest_dependency(self) -> None:
        args = SimpleNamespace(
            database="sec_core",
            artifact_root_win="D:/market-data/sec_core",
            output_root_win="D:/market-data/prepared/sec_core",
            sources="submissions,companyfacts",
            storage_policy="live_market_ssd",
            clickhouse_file_root="/mnt/d/market-data",
            limit_ciks=0,
            max_threads=32,
            max_memory_usage="96G",
            minimum_row_ratio=0.95,
            dry_run=False,
        )
        output = StringIO()

        with redirect_stdout(output):
            bulk_ingest.print_header(args, [], [], Path("report.jsonl"))

        rendered = output.getvalue()
        self.assertIn("snapshot_mode=replace", rendered)
        self.assertIn("limit_members=0", rendered)
        self.assertNotIn("member_manifest_enabled", rendered)
        self.assertNotIn("batch_size=", rendered)

    def test_partial_ticker_snapshot_is_rejected_before_replacement(self) -> None:
        artifact = bulk_ingest.SourceArtifact("company_tickers", "company_tickers", "url", Path("x.json"), "id", 1, "sha")
        args = SimpleNamespace()

        with self.assertRaisesRegex(RuntimeError, "requires all ticker snapshots"):
            snapshot_refresh.refresh_selected_snapshots(SimpleNamespace(), args, [artifact], "run", Path("report"), None)

    def test_snapshot_cutover_rolls_back_when_activation_manifest_fails(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.exchanges: list[str] = []

            def execute(self, sql: str) -> str:
                if sql.startswith("SELECT count()"):
                    return "100\n"
                if sql.startswith("EXCHANGE TABLES"):
                    self.exchanges.append(sql)
                    return ""
                if sql.startswith("DROP TABLE"):
                    return ""
                raise AssertionError(sql)

        client = FakeClient()
        args = SimpleNamespace(database="sec_core", minimum_row_ratio=0.95)

        with self.assertRaisesRegex(RuntimeError, "manifest failed"):
            snapshot_refresh.validate_and_cut_over(
                client,
                args,
                ["mirror_a", "mirror_b"],
                {"mirror_a": 100, "mirror_b": 100},
                run_id="test",
                on_active=lambda: (_ for _ in ()).throw(RuntimeError("manifest failed")),
            )

        self.assertEqual(len(client.exchanges), 4)
        self.assertEqual(client.exchanges[0], client.exchanges[-1])
        self.assertEqual(client.exchanges[1], client.exchanges[-2])

    def test_sec_acceptance_parser_distinguishes_api_utc_and_sgml_eastern(self) -> None:
        self.assertEqual(
            submissions.parse_acceptance_datetime("2026-02-05T21:08:23.000Z"),
            "2026-02-05T21:08:23.000000000Z",
        )
        self.assertEqual(submissions.parse_acceptance_datetime("20260205160823"), "2026-02-05T21:08:23.000000000Z")
        self.assertEqual(submissions.parse_acceptance_datetime("20260317141633"), "2026-03-17T18:16:33.000000000Z")
        self.assertIsNone(submissions.parse_acceptance_datetime("2026-03-17 18:16:33"))
        self.assertIsNone(submissions.parse_acceptance_datetime("20261101013000"))

    def test_sec_chunks_are_complete_when_max_chunks_is_zero(self) -> None:
        chunks = tokens.make_sec_chunks(list(range(10_001)), chunk_tokens=1024, max_chunks=0)

        self.assertEqual(len(chunks), 10)
        self.assertEqual(chunks[-1].token_end, 10_001)
        self.assertTrue(all(chunk.was_truncated == 0 for chunk in chunks))

    def test_positive_max_chunks_still_caps_news(self) -> None:
        chunks = tokens.make_news_chunks(list(range(10_001)), chunk_tokens=1024, max_chunks=2)

        self.assertEqual(len(chunks), 2)
        self.assertTrue(all(chunk.was_truncated == 1 for chunk in chunks))

    def test_sec_v3_schema_supports_long_documents_and_time_provenance(self) -> None:
        token_sql = tokens.create_sec_token_table_sql("market_sip_compact", "sec_filing_text_tokens_v3", "")
        embedding_sql = tokens.create_sec_embedding_table_sql("market_sip_compact", "sec_filing_text_embeddings_v3", "")

        for sql in (token_sql, embedding_sql):
            self.assertIn("token_chunk_index UInt16", sql)
            self.assertIn("accepted_at_source LowCardinality(String)", sql)
            self.assertIn("event_time_quality LowCardinality(String)", sql)

    def test_embedding_source_excludes_date_only_fallback_times(self) -> None:
        sql = tokens.sec_rendered_source_ctes_sql(
            source_database="q_live",
            filing_table="sec_filing_v3",
            document_table="sec_filing_document_v3",
            rendered_text_table="sec_filing_text_rendered_v3",
            bridge_table="id_sec_market_bridge_v3",
            start_sql="toDateTime64('2026-07-01', 9, 'UTC')",
            end_sql="toDateTime64('2026-07-02', 9, 'UTC')",
        )

        self.assertIn("f.accepted_at_source NOT IN", sql)
        self.assertIn("archive_filing_date_midnight", sql)
        self.assertIn("'exact' AS event_time_quality", sql)


if __name__ == "__main__":
    unittest.main()
