from __future__ import annotations

import json
from io import StringIO
import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

from pipelines.sec.edgar.sec_pipeline.clickhouse_writer import SecClickHouseWriter, SecWriteResult
from pipelines.sec.edgar.sec_pipeline.feed import SecFeedItem
from pipelines.sec.edgar.sec_pipeline.http import SecHttpError, SecHttpResponse
from pipelines.sec.edgar.sec_pipeline.live_ingest_manifest import LiveIngestManifestConfig, SecLiveIngestManifest
from pipelines.sec.edgar.sec_pipeline.submissions import SecSubmissionsClient
from pipelines.sec.edgar.sec_pipeline.xbrl_live import SecLiveXbrlExtractor
from services.sec_gateway.gateway import SecGateway, live_pending_source_reasons
from services.sec_gateway.terminal import runtime_panel, sec_pipeline_panel
from rich.console import Console


ACCESSION_OLD = "0000000123-26-000001"
ACCESSION_NEW = "0000000123-26-000002"


class SequenceHttp:
    def __init__(self, responses: list[dict[str, object] | SecHttpError]) -> None:
        self.responses = list(responses)
        self.calls = 0

    def get(self, url: str) -> SecHttpResponse:
        self.calls += 1
        response = self.responses.pop(0)
        if isinstance(response, SecHttpError):
            raise response
        return SecHttpResponse(url=url, status=200, content_type="application/json", body=json.dumps(response).encode())


def submissions_payload(*accessions: str) -> dict[str, object]:
    return {
        "cik": 123,
        "name": "Example",
        "filings": {
            "recent": {
                "accessionNumber": list(accessions),
                "form": ["10-Q"] * len(accessions),
                "filingDate": ["2026-07-21"] * len(accessions),
                "reportDate": ["2026-06-30"] * len(accessions),
                "acceptanceDateTime": ["2026-07-21T15:01:02.000Z"] * len(accessions),
                "primaryDocument": ["example.htm"] * len(accessions),
                "primaryDocDescription": ["Quarterly report"] * len(accessions),
                "size": [100] * len(accessions),
                "items": [""] * len(accessions),
                "isXBRL": [1] * len(accessions),
                "isInlineXBRL": [1] * len(accessions),
            }
        },
    }


def companyfacts_payload(*accessions: str) -> dict[str, object]:
    return {
        "entityName": "Example",
        "facts": {
            "us-gaap": {
                "Assets": {
                    "label": "Assets",
                    "description": "Assets",
                    "units": {
                        "USD": [
                            {
                                "accn": accession,
                                "val": 10,
                                "end": "2026-06-30",
                                "filed": "2026-07-21",
                                "form": "10-Q",
                            }
                            for accession in accessions
                        ]
                    },
                }
            }
        },
    }


class SecLiveIngestRecoveryTests(unittest.TestCase):
    def test_submissions_cache_refreshes_once_when_new_accession_is_absent(self) -> None:
        http = SequenceHttp([submissions_payload(ACCESSION_OLD), submissions_payload(ACCESSION_OLD, ACCESSION_NEW)])
        client = SecSubmissionsClient(http=http, max_cache_entries=2, max_cache_age_seconds=3600)
        client.fetch_payload(cik="123")

        filing = client.fetch_recent_filing(cik="123", accession_number=ACCESSION_NEW)

        self.assertIsNotNone(filing)
        self.assertEqual(filing.accession_number, ACCESSION_NEW)
        self.assertEqual(filing.accepted_at_utc, "2026-07-21T15:01:02.000000000Z")
        self.assertEqual(http.calls, 2)

    def test_companyfacts_cache_refreshes_once_when_new_accession_is_absent(self) -> None:
        http = SequenceHttp([companyfacts_payload(ACCESSION_OLD), companyfacts_payload(ACCESSION_OLD, ACCESSION_NEW)])
        extractor = SecLiveXbrlExtractor(http=http, max_payload_cache_entries=2, max_payload_cache_age_seconds=3600)
        old = extractor.extract_for_accession(
            cik="123", accession_number=ACCESSION_OLD, source_run_id="run", inserted_at="2026-07-21T15:00:00Z"
        )

        new = extractor.extract_for_accession(
            cik="123", accession_number=ACCESSION_NEW, source_run_id="run", inserted_at="2026-07-21T15:00:00Z"
        )

        self.assertEqual(old.matched_facts, 1)
        self.assertEqual(new.matched_facts, 1)
        self.assertEqual(new.companyfacts_status, "available")
        self.assertEqual(http.calls, 2)

    def test_companyfacts_404_negative_cache_expires(self) -> None:
        error = SecHttpError(status=404, url="https://example.test", body=b"missing")
        http = SequenceHttp([error, companyfacts_payload(ACCESSION_NEW)])
        extractor = SecLiveXbrlExtractor(
            http=http,
            max_missing_cik_cache_entries=2,
            max_missing_cik_cache_age_seconds=300,
        )
        with patch("pipelines.sec.edgar.sec_pipeline.xbrl_live.time.monotonic", return_value=0):
            first = extractor.extract_for_accession(
                cik="123", accession_number=ACCESSION_NEW, source_run_id="run", inserted_at="2026-07-21T15:00:00Z"
            )
        with patch("pipelines.sec.edgar.sec_pipeline.xbrl_live.time.monotonic", return_value=100):
            cached = extractor.extract_for_accession(
                cik="123", accession_number=ACCESSION_NEW, source_run_id="run", inserted_at="2026-07-21T15:00:00Z"
            )
        with patch("pipelines.sec.edgar.sec_pipeline.xbrl_live.time.monotonic", return_value=301):
            recovered = extractor.extract_for_accession(
                cik="123", accession_number=ACCESSION_NEW, source_run_id="run", inserted_at="2026-07-21T15:00:00Z"
            )

        self.assertEqual(first.companyfacts_status, "missing_404")
        self.assertEqual(cached.companyfacts_status, "missing_404")
        self.assertEqual(recovered.matched_facts, 1)
        self.assertEqual(http.calls, 2)

    def test_manifest_completion_is_the_only_existing_accession_authority(self) -> None:
        class FakeManifest:
            def completed_revisions(self, _accessions: list[str]) -> dict[str, datetime]:
                return {ACCESSION_OLD: datetime(2026, 7, 21, 15, 0, tzinfo=UTC)}

        gateway = SecGateway.__new__(SecGateway)
        gateway._live_manifest = FakeManifest()
        gateway.config = SimpleNamespace(xbrl_context_sync_enabled=False)
        items = [
            SecFeedItem(ACCESSION_OLD, ACCESSION_OLD.replace("-", ""), "0000000123", "10-Q", "", "", "", datetime(2026, 7, 21, 14, 0, tzinfo=UTC)),
            SecFeedItem(ACCESSION_NEW, ACCESSION_NEW.replace("-", ""), "0000000123", "10-Q", "", "", "", datetime(2026, 7, 21, 14, 0, tzinfo=UTC)),
        ]

        self.assertEqual(gateway._existing_accessions(items), {ACCESSION_OLD})

    def test_completed_revision_comparison_uses_manifest_millisecond_precision(self) -> None:
        class FakeManifest:
            def completed_revisions(self, _accessions: list[str]) -> dict[str, datetime]:
                return {ACCESSION_NEW: datetime(2026, 7, 21, 15, 0, 0, 123000, tzinfo=UTC)}

        gateway = SecGateway.__new__(SecGateway)
        gateway._live_manifest = FakeManifest()
        gateway.config = SimpleNamespace(xbrl_context_sync_enabled=False)
        item = SecFeedItem(
            ACCESSION_NEW,
            ACCESSION_NEW.replace("-", ""),
            "0000000123",
            "10-Q",
            "",
            "",
            "",
            datetime(2026, 7, 21, 15, 0, 0, 123999, tzinfo=UTC),
        )

        self.assertEqual(gateway._existing_accessions([item]), {ACCESSION_NEW})

    def test_completed_revision_query_filters_to_complete_status(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.sql = ""

            def execute(self, sql: str) -> str:
                self.sql = sql
                return f"{ACCESSION_NEW}\t2026-07-21 15:01:02.000\n"

        client = FakeClient()
        manifest = SecLiveIngestManifest(client, LiveIngestManifestConfig())

        rows = manifest.completed_revisions([ACCESSION_NEW])

        self.assertIn("status = 'complete'", client.sql)
        self.assertIn("renderer_version = 'sec_packed_text_renderer_v9'", client.sql)
        self.assertEqual(rows[ACCESSION_NEW], datetime(2026, 7, 21, 15, 1, 2, tzinfo=UTC))

    def test_manifest_schema_migration_adds_renderer_version(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.sql: list[str] = []

            def execute(self, sql: str) -> str:
                self.sql.append(sql)
                return ""

        client = FakeClient()
        manifest = SecLiveIngestManifest(client, LiveIngestManifestConfig())

        manifest.ensure_table()

        self.assertEqual(len(client.sql), 2)
        self.assertIn("renderer_version LowCardinality(String)", client.sql[0])
        self.assertIn("ADD COLUMN IF NOT EXISTS renderer_version", client.sql[1])

    def test_pending_source_retry_window_is_durable(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.sql = ""

            def execute(self, sql: str) -> str:
                self.sql = sql
                return ACCESSION_NEW + "\n"

        client = FakeClient()
        manifest = SecLiveIngestManifest(client, LiveIngestManifestConfig())

        rows = manifest.deferred_accessions([ACCESSION_NEW])

        self.assertEqual(rows, {ACCESSION_NEW})
        self.assertIn("status = 'pending_source'", client.sql)
        self.assertIn("retry_after_utc > now64", client.sql)

    def test_terminal_exposes_pending_and_deferred_ingest_state_at_compact_width(self) -> None:
        gateway = SimpleNamespace(
            current_poll_seconds=lambda: 30.0,
            config=SimpleNamespace(pipeline=SimpleNamespace()),
        )
        metrics = {
            "live_pending_filings": 2,
            "live_deferred_filings": 3,
            "live_worker_failures": 0,
            "live_completed_filings": 5,
            "written_filings": 7,
            "live_queued_filings": 9,
        }
        output = StringIO()
        console = Console(file=output, width=90, height=24, force_terminal=False, color_system=None)

        console.print(sec_pipeline_panel(gateway, metrics, compact=True))
        console.print(runtime_panel(gateway, metrics))

        rendered = output.getvalue()
        self.assertIn("pending 2", rendered)
        self.assertIn("deferred 3", rendered)

    def test_writer_replays_same_revision_when_completion_is_pending(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.inserts: list[str] = []

            def execute(self, sql: str) -> str:
                if sql.startswith("INSERT INTO"):
                    self.inserts.append(sql)
                    return ""
                raise AssertionError(f"same-revision replay must not query document completion: {sql}")

        key = {"cik": "0000000123", "accession_number": ACCESSION_NEW, "document_id": "doc-1"}
        client = FakeClient()
        writer = SecClickHouseWriter(client, database="q_live")

        result = writer.write_accession(
            filing_row={"cik": "0000000123", "accession_number": ACCESSION_NEW},
            entity_rows=[],
            archive_accession_rows=[],
            document_rows=[{**key, "source_revision_rank": 123}],
            text_source_rows=[key],
            text_rows=[key],
            skip_rows=[],
            skip_existing=False,
            skip_same_revision=False,
        )

        self.assertFalse(result.skipped_existing)
        self.assertEqual(len(client.inserts), 4)

    def test_expected_xbrl_without_facts_remains_pending(self) -> None:
        calls: list[str] = []

        class FakeManifest:
            def mark_pending(self, **_kwargs) -> None:
                calls.append("pending")

            def mark_pending_source(self, **_kwargs) -> None:
                calls.append("pending_source")

            def mark_complete(self, **_kwargs) -> None:
                calls.append("complete")

            def mark_failed(self, **_kwargs) -> None:
                calls.append("failed")

        gateway = SecGateway.__new__(SecGateway)
        gateway.config = SimpleNamespace(execute=True, xbrl_context_sync_enabled=True, source_retry_seconds=300.0)
        gateway._run_id = "run"
        gateway._live_pipeline = SimpleNamespace(
            process_feed_item=lambda *_args, **_kwargs: SimpleNamespace(
                filing_row={"cik": "0000000123", "accession_number": ACCESSION_NEW},
                entity_rows=[],
                archive_accession_rows=[],
                document_rows=[],
                text_source_rows=[],
                text_rows=[],
                skip_rows=[],
                pac_rows=[],
                xbrl_rows=SimpleNamespace(
                    concept_rows=[], company_fact_rows=[], frame_rows=[], frame_observation_rows=[],
                    companyfacts_status="pending_accession",
                ),
                source_cik="0000000123",
                source_version_key="revision-key",
                source_revision_at="2026-07-21 15:01:02.000",
                source_revision_rank=123,
                metadata_status="submissions_recent",
                xbrl_expected=True,
            )
        )
        gateway._live_manifest = FakeManifest()
        gateway._writer = SimpleNamespace(write_accession=lambda **_kwargs: SecWriteResult(filing_rows=1))
        gateway._xbrl_context = SimpleNamespace()
        gateway._log = lambda *_args, **_kwargs: None
        item = SecFeedItem(
            ACCESSION_NEW,
            ACCESSION_NEW.replace("-", ""),
            "0000000123",
            "10-Q",
            "",
            "",
            "",
            datetime(2026, 7, 21, 15, 1, 2, tzinfo=UTC),
        )

        result = gateway._process_item(item, set())

        self.assertEqual(result.ingest_status, "pending_source")
        self.assertEqual(calls, ["pending", "pending_source"])

    def test_date_only_acceptance_fallback_remains_pending_for_submissions_retry(self) -> None:
        rows = SimpleNamespace(
            xbrl_expected=False,
            xbrl_rows=SimpleNamespace(companyfacts_status="not_requested"),
            metadata_status="submissions_not_found",
            filing_row={"accepted_at_source": "archive_filing_date_midnight"},
        )

        reasons = live_pending_source_reasons(rows, expected_facts=0)

        self.assertEqual(reasons, ["SEC submissions metadata does not yet provide an exact acceptance timestamp"])


if __name__ == "__main__":
    unittest.main()
