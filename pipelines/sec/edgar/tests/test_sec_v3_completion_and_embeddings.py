from __future__ import annotations

import json
import tempfile
import unittest
import sys
import tarfile
import zipfile
from contextlib import redirect_stdout
from datetime import date
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from pipelines.market_sip.events import clickhouse_build_text_tokens as tokens
from pipelines.sec.edgar import sec_acceptance_raw_metadata_repair as acceptance_repair
from pipelines.sec.edgar import sec_archive_identity_audit as archive_identity
from pipelines.sec.edgar import sec_acceptance_fragment_fill as acceptance_fragment
from pipelines.sec.edgar import sec_acceptance_backfill_build as acceptance_build
from pipelines.sec.edgar import sec_bulk_clickhouse_ingest as bulk_ingest
from pipelines.sec.edgar import sec_bulk_to_canonical as bulk_canonical
from pipelines.sec.edgar import sec_bulk_snapshot_refresh as snapshot_refresh
from pipelines.sec.edgar import sec_filing_parent_reconcile as parent_reconcile
from pipelines.sec.edgar import sec_historical_gap_fill as historical
from pipelines.sec.edgar import sec_filing_text_extract_parts as filing_extract
from pipelines.sec.edgar.sec_bulk_sources import BULK_SOURCE_NAMES, DEFAULT_BULK_SOURCES, require_complete_bulk_sources
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
        self.assertEqual(args.bulk_sources, DEFAULT_BULK_SOURCES)
        for stage in ("bulk-download", "bulk-ingest"):
            source_index = commands[stage].index("--sources") + 1
            self.assertEqual(commands[stage][source_index], DEFAULT_BULK_SOURCES)

    def test_historical_bulk_source_contract_rejects_partial_snapshots(self) -> None:
        self.assertEqual(require_complete_bulk_sources("all"), DEFAULT_BULK_SOURCES)
        self.assertEqual(tuple(DEFAULT_BULK_SOURCES.split(",")), BULK_SOURCE_NAMES)
        with self.assertRaisesRegex(ValueError, "complete bulk snapshot"):
            require_complete_bulk_sources("submissions,companyfacts")

    def test_acceptance_repair_executes_only_in_execute_mode(self) -> None:
        command = ["python", "sec_acceptance_raw_metadata_repair.py"]

        self.assertEqual(historical.add_execute_flag(command, SimpleNamespace(execute=False)), command)
        self.assertEqual(
            historical.add_execute_flag(command, SimpleNamespace(execute=True)),
            [*command, "--execute"],
        )

    def test_historical_fill_runs_direct_acceptance_enrichment(self) -> None:
        with mock.patch.object(sys, "argv", ["sec_historical_gap_fill.py", "--execute"]):
            args = historical.parse_args()
        commands = {command.stage: command.command for command in historical.build_commands(args, Path("logs"))}

        self.assertIn("acceptance-submissions-enrichment", commands)
        self.assertIn("--execute", commands["acceptance-submissions-enrichment"])
        self.assertIn("filing-parent-reconcile", commands)
        self.assertIn("--execute", commands["filing-parent-reconcile"])
        self.assertIn("archive-identity-audit", commands)
        stage_index = commands["bulk-canonicalize"].index("--stages") + 1
        self.assertEqual(commands["bulk-canonicalize"][stage_index], "xbrl")
        repair = commands["acceptance-raw-metadata-repair"]
        self.assertIn("--enriched-table", repair)
        self.assertIn("sec_submissions_filing_overlay_v3", repair)

    def test_bulk_canonicalizer_rejects_submission_parent_materialization(self) -> None:
        self.assertEqual(bulk_canonical.parse_stages("xbrl"), ["xbrl"])
        with self.assertRaisesRegex(SystemExit, "invalid --stages"):
            bulk_canonical.parse_stages("parents,xbrl")

    def test_bulk_download_is_always_reconciled(self) -> None:
        command = historical.StageCommand("bulk-download", [], Path("bulk.log"), False, ("covered",))

        self.assertFalse(historical.stage_already_completed(SimpleNamespace(), command))

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
        self.assertIn("sec_core_submissions_bulk_raw_z_repair", cte)
        self.assertIn("sec_submissions_filing_overlay_v3", cte)
        self.assertIn("source_priority", cte)
        self.assertNotIn("sec_filing_v2", cte)
        self.assertIn("UNION ALL", cte)
        self.assertIn("r.accepted_at_utc AS accepted_at_utc", sql)
        self.assertIn("r.acceptance_datetime_raw AS acceptance_datetime_raw", sql)
        self.assertIn("f.accepted_at_source IN", sql)
        self.assertIn("max_partitions_per_insert_block = 1000", sql)
        self.assertIn("max_threads = 32", sql)
        self.assertIn("ALTER TABLE `q_live`.`sec_filing_v3`", delete_sql)
        self.assertIn("accepted_at_source IN", delete_sql)
        self.assertIn("FROM `sec_core`.`sec_bulk_mirror_filing_v3` FINAL", delete_sql)
        self.assertIn("FROM `sec_core`.`sec_submissions_filing_overlay_v3` FINAL", delete_sql)
        self.assertIn("mutations_sync = 2", delete_sql)

    def test_direct_submissions_match_uses_requested_filing_cik_not_accession_prefix(self) -> None:
        payload = {
            "cik": "0000766421",
            "name": "ALASKA AIR GROUP, INC.",
            "filings": {
                "recent": {
                    "accessionNumber": ["0002143285-26-000002"],
                    "acceptanceDateTime": ["2026-07-07T23:25:29.000Z"],
                    "filingDate": ["2026-07-07"],
                    "reportDate": [""],
                    "form": ["3"],
                    "primaryDocument": ["xslF345X06/wk-form3_1783466725.xml"],
                    "size": [702453],
                    "items": [""],
                },
                "files": [],
            },
        }
        with tempfile.TemporaryDirectory() as temp_root:
            job = acceptance_fragment.DirectSubmissionJob(
                cik="0000766421",
                url="https://data.sec.gov/submissions/CIK0000766421.json",
                artifact_path=str(Path(temp_root) / "CIK0000766421.json"),
                wanted_accessions=("0002143285-26-000002",),
            )
            with mock.patch.object(acceptance_fragment, "fetch_url", return_value=json.dumps(payload).encode("utf-8")):
                result = acceptance_fragment.process_direct_submission_job(
                    job,
                    "test@example.com",
                    30.0,
                    0,
                    0.0,
                    acceptance_fragment.RateLimiter(0.0),
                )

        self.assertEqual(result.status, "ok")
        self.assertEqual(len(result.matched_rows), 1)
        self.assertEqual(result.matched_rows[0]["cik"], "0000766421")
        self.assertEqual(result.matched_rows[0]["accession_number"], "0002143285-26-000002")
        self.assertEqual(result.matched_rows[0]["acceptance_datetime_raw"], "2026-07-07T23:25:29.000Z")
        self.assertEqual(result.matched_rows[0]["accepted_at_source"], "submissions_api_recent")

    def test_direct_submission_404_is_source_not_found_not_transport_failure(self) -> None:
        job = acceptance_fragment.DirectSubmissionJob(
            cik="0002056317",
            url="https://data.sec.gov/submissions/CIK0002056317.json",
            artifact_path="CIK0002056317.json",
            wanted_accessions=("0002056317-25-000001",),
        )
        with mock.patch.object(
            acceptance_fragment,
            "fetch_url",
            side_effect=acceptance_fragment.SecSourceNotFound("HTTP 404: Not Found"),
        ):
            result = acceptance_fragment.process_direct_submission_job(
                job,
                "test@example.com",
                30.0,
                0,
                0.0,
                acceptance_fragment.RateLimiter(0.0),
            )

        self.assertEqual(result.status, "not_found")
        self.assertEqual(result.matched_rows, ())

    def test_archive_parser_uses_embedded_issuer_cik_not_accession_prefix(self) -> None:
        raw = b"""<SEC-DOCUMENT>0002143285-26-000002.txt
<SEC-HEADER>
<ACCESSION-NUMBER>0002143285-26-000002
<FILING-DATE>20260707
<ISSUER>
<COMPANY-DATA>
<CONFORMED-NAME>ALASKA AIR GROUP, INC.
<CIK>0000766421
</COMPANY-DATA>
</ISSUER>
<REPORTING-OWNER>
<OWNER-DATA>
<CONFORMED-NAME>REPORTING OWNER
<CIK>0002143285
</OWNER-DATA>
</REPORTING-OWNER>
</SEC-HEADER>
<DOCUMENT>
<TYPE>3
<SEQUENCE>1
<FILENAME>form3.xml
<TEXT><ownershipDocument/></TEXT>
</DOCUMENT>
"""

        parsed = filing_extract.parse_filing(raw, "0002143285-26-000002.nc")

        self.assertEqual(parsed["accession_number"], "0002143285-26-000002")
        self.assertEqual(parsed["cik"], "0000766421")

    def test_archive_parent_resolution_never_falls_back_by_accession(self) -> None:
        wrong_parent = filing_extract.FilingParent(
            filing_id="owner-parent",
            accession_number="0002143285-26-000002",
            accession_number_compact="000214328526000002",
            cik="0002143285",
            form_type="3",
            accepted_at_utc="2026-07-07 23:25:29",
            primary_document="form3.xml",
            primary_document_url="",
            filing_detail_url="",
        )
        parents = {(wrong_parent.cik, wrong_parent.accession_number): wrong_parent}

        resolved = filing_extract.resolve_parent(
            parents,
            {"cik": "0000766421", "accession_number": "0002143285-26-000002"},
        )

        self.assertIsNone(resolved)

    def test_archive_identity_audit_compares_stored_and_sgml_cik(self) -> None:
        raw = b"""<SEC-DOCUMENT>0002143285-26-000002.txt
<SEC-HEADER><ACCESSION-NUMBER>0002143285-26-000002
<ISSUER><COMPANY-DATA><CIK>0000766421
</COMPANY-DATA></ISSUER></SEC-HEADER>
<DOCUMENT><TYPE>3<SEQUENCE>1<FILENAME>form3.xml<TEXT>x</TEXT></DOCUMENT>
"""
        with tempfile.TemporaryDirectory() as temp_root:
            archive_path = Path(temp_root) / "sample.nc.tar.gz"
            source_path = Path(temp_root) / "sample.nc"
            source_path.write_bytes(raw)
            with tarfile.open(archive_path, "w:gz") as archive:
                archive.add(source_path, arcname="./0002143285-26-000002.nc")
            result = archive_identity.audit_archive(
                str(archive_path),
                {
                    "0002143285-26-000002.nc": {
                        "cik": "0000766421",
                        "accession_number": "0002143285-26-000002",
                    }
                },
            )

        self.assertEqual(result["matched"], 1)
        self.assertEqual(result["mismatched"], 0)

    def test_direct_submission_overlay_preserves_relation_without_acceptance_time(self) -> None:
        statements: list[str] = []
        client = SimpleNamespace(execute=statements.append)
        row = {
            "acceptance_id": "id",
            "cik": "0000766421",
            "accession_number": "0002143285-26-000002",
            "accepted_at_utc": None,
        }

        inserted = acceptance_build.insert_rows(client, "sec_core", "sec_submissions_filing_overlay_v3", [row])

        self.assertEqual(inserted, 1)
        self.assertIn('"accepted_at_utc":null', statements[0])
        self.assertIn("sec_submissions_filing_overlay_v3", acceptance_build.stage_table_sql("sec_core", "sec_submissions_filing_overlay_v3", ""))
        self.assertIn("accepted_at_utc Nullable", acceptance_build.stage_table_sql("sec_core", "sec_submissions_filing_overlay_v3", ""))

    def test_parent_reconciliation_excludes_every_dependent_table(self) -> None:
        sql = parent_reconcile.candidates_select_sql("q_live", "sec_filing_v3")

        for table in parent_reconcile.DEPENDENT_TABLES:
            self.assertIn(f"`q_live`.`{table}`", sql)
        self.assertIn("assumeNotNull(p.cik) AS cik", sql)
        self.assertIn("assumeNotNull(p.accession_number) AS accession_number", sql)
        self.assertIn("p.text_status = 'submissions_bulk_parent'", sql)
        self.assertIn("HAVING uniqExact(cik) = 1", sql)

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
        self.assertIn("compatibility_repaired_members UInt64", ddl)
        self.assertIn("compatibility_repaired_values UInt64", ddl)

    def test_companyfacts_json_normalizer_repairs_only_oversized_fact_values(self) -> None:
        oversized = 999_999_999_999_000_000_000
        source = json.dumps(
            {
                "cik": oversized,
                "facts": {"us-gaap": {"Shares": {"units": {"shares": [{"val": oversized}, {"val": 10}]}}}},
                "description": f'embedded \\"val\\": {oversized}',
            }
        )

        normalized, repaired_values = snapshot_refresh.normalize_companyfacts_json(source)
        parsed = json.loads(normalized)

        self.assertEqual(repaired_values, 1)
        self.assertEqual(parsed["cik"], oversized)
        self.assertEqual(parsed["facts"]["us-gaap"]["Shares"]["units"]["shares"][0]["val"], str(oversized))
        self.assertEqual(parsed["facts"]["us-gaap"]["Shares"]["units"]["shares"][1]["val"], 10)
        self.assertEqual(parsed["description"], f'embedded \\"val\\": {oversized}')

    def test_companyfacts_repaired_sql_uses_structured_observation_extraction(self) -> None:
        args = SimpleNamespace(max_threads=32, max_memory_usage="96G")
        artifact = SimpleNamespace(source_file_id="source")
        sql = snapshot_refresh.companyfacts_insert_sql(
            stage="stage",
            raw="raw",
            artifact=artifact,
            now="now64(9, 'UTC')",
            args=args,
            fact_type="Tuple(val Nullable(Float64))",
            compatibility_repaired=True,
        )

        self.assertIn("JSONExtractArrayRaw(unit_pair.2)", sql)
        self.assertIn("JSON_VALUE(fact_json, '$.val')", sql)
        self.assertIn("toFloat64OrNull", sql)
        self.assertIn("compatibility_repaired = 1", sql)
        self.assertNotIn("ARRAY JOIN JSONExtract(unit_pair.2", sql)

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
