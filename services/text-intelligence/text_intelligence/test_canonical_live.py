from __future__ import annotations

import unittest
import asyncio
from unittest import mock

from .canonical_live import CanonicalTextRuntime, LoadedSource, TextDocumentNotice


class CanonicalTextRuntimeTests(unittest.TestCase):
    def test_news_runtime_has_no_legacy_news_classifier_dependency(self) -> None:
        source = __import__("inspect").getsource(__import__("text_intelligence.canonical_live", fromlist=["*"]))
        self.assertNotIn("classify_news_document", source)
        self.assertNotIn("news_classification", source)
        self.assertNotIn("scoped_labeling_v1", source)

    def test_sec_reload_passes_physical_document_partition(self) -> None:
        class Client:
            def iter_json_each_row(self, _sql):
                return iter(
                    (
                        {
                            "filing_id": "filing-1",
                            "cik": "0000000001",
                            "accession_number": "0000000001-26-000001",
                            "document_partition": 37,
                            "source_timestamp": "2026-07-29 12:00:00",
                            "company_name": "Example Corp",
                            "form_type": "8-K",
                            "filing_items": "8.01",
                            "filing_date": "2026-07-29",
                            "report_date": "2026-07-29",
                            "accepted_at_source": "20260729120000",
                        },
                    )
                )

        runtime = CanonicalTextRuntime(
            client=Client(),
            database="q_live",
            live_news=mock.Mock(),
        )
        with (
            mock.patch(
                "text_intelligence.canonical_live.extend_sec_ticker_mappings"
            ),
            mock.patch(
                "text_intelligence.canonical_live."
                "iter_sec_documents_for_filings",
                return_value=iter(()),
            ) as documents,
        ):
            loaded = runtime._load_sec(
                TextDocumentNotice(
                    corpus="sec",
                    source_id="0000000001-26-000001",
                    source_cik="0000000001",
                )
            )

        self.assertEqual(loaded.rows, ())
        self.assertEqual(documents.call_args.kwargs["partition"], 37)

    def test_news_reload_is_bounded_by_notice_date(self) -> None:
        class Client:
            sql = ""

            def iter_json_each_row(self, sql):
                self.sql = sql
                return iter(())

        client = Client()
        runtime = CanonicalTextRuntime(
            client=client,
            database="q_live",
            live_news=mock.Mock(),
        )

        runtime._load_news(
            TextDocumentNotice(
                corpus="news",
                source_id="news-id",
                source_timestamp="2026-07-31T13:45:00Z",
            )
        )

        self.assertEqual(client.sql.count("published_date=toDate('2026-07-31')"), 2)
        self.assertIn("canonical_news_id='news-id'", client.sql)
        self.assertIn("max_execution_time=25", client.sql)

    def test_news_reload_rejects_unbounded_notice(self) -> None:
        runtime = CanonicalTextRuntime(
            client=mock.Mock(),
            database="q_live",
            live_news=mock.Mock(),
        )

        with self.assertRaisesRegex(ValueError, "requires source_timestamp"):
            runtime._load_news(
                TextDocumentNotice(corpus="news", source_id="news-id")
            )

    def test_sec_reload_requires_and_uses_exact_cik(self) -> None:
        class Client:
            sql = ""

            def iter_json_each_row(self, sql):
                self.sql = sql
                return iter(())

        client = Client()
        runtime = CanonicalTextRuntime(
            client=client,
            database="q_live",
            live_news=mock.Mock(),
        )

        with self.assertRaisesRegex(ValueError, "requires source_cik"):
            runtime._load_sec(
                TextDocumentNotice(
                    corpus="sec", source_id="0000000001-26-000001"
                )
            )
        loaded = runtime._load_sec(
            TextDocumentNotice(
                corpus="sec",
                source_id="0000000001-26-000001",
                source_cik="0000000001",
            )
        )

        self.assertIn("PREWHERE cik='0000000001'", client.sql)
        self.assertIn("accession_number='0000000001-26-000001'", client.sql)
        self.assertIn("max_execution_time=25", client.sql)
        self.assertEqual(loaded.disposition, "not_ready")

    def test_sec_non_narrative_document_is_durably_ineligible(self) -> None:
        class Client:
            calls = 0

            def iter_json_each_row(self, _sql):
                self.calls += 1
                if self.calls == 1:
                    return iter(
                        (
                            {
                                "filing_id": "filing-1",
                                "cik": "0001217286",
                                "accession_number": "0002071691-26-017010",
                                "document_partition": 12,
                                "source_timestamp": "2026-07-28 14:09:56",
                                "company_name": "Example Fund",
                                "form_type": "NPORT-P",
                                "filing_items": "",
                                "filing_date": "2026-07-28",
                                "report_date": "2026-06-30",
                                "accepted_at_source": "live",
                                "source_content_sha256": "abc123",
                            },
                        )
                    )
                return iter(
                    (
                        {
                            "document_id": "primary",
                            "cik": "0001217286",
                            "accession_number": "0002071691-26-017010",
                            "sequence_number": "1",
                            "document_type": "NPORT-P",
                            "document_role": "primary_document",
                            "description": "",
                            "document_name": "primary_doc.xml",
                        },
                    )
                )

        runtime = CanonicalTextRuntime(
            client=Client(), database="q_live", live_news=mock.Mock()
        )
        notice = TextDocumentNotice(
            corpus="sec",
            source_id="0002071691-26-017010",
            source_cik="0001217286",
        )

        loaded = runtime._load_sec(notice)

        self.assertEqual(loaded.disposition, "ineligible")
        self.assertEqual(len(loaded.source_hash), 64)
        with (
            mock.patch.object(runtime, "_load_source", return_value=loaded),
            mock.patch.object(runtime, "_status_is_current", return_value=False),
            mock.patch.object(runtime, "_write_status") as write_status,
        ):
            outcome = runtime._process_notice(notice)
        self.assertEqual(outcome, "skipped_ineligible")
        self.assertEqual(runtime.metrics["deterministic_ineligible"], 1)
        self.assertEqual(runtime.metrics["deterministic_completed"], 1)
        write_status.assert_called_once_with(
            notice, loaded.source_hash, "complete", 0, 0, ""
        )

    def test_active_failures_clear_only_when_same_source_recovers(self) -> None:
        runtime = CanonicalTextRuntime(
            client=mock.Mock(),
            database="q_live",
            live_news=mock.Mock(),
        )
        failed = TextDocumentNotice(
            corpus="news",
            source_id="failed-news",
            source_timestamp="2026-07-31T13:45:00Z",
        )
        other = TextDocumentNotice(
            corpus="news",
            source_id="other-news",
            source_timestamp="2026-07-31T13:46:00Z",
        )

        runtime._record_failure(failed, "TimeoutError: timed out")
        runtime._resolve_failure(other)
        self.assertEqual(
            runtime.snapshot_metrics()["deterministic_active_failure_count"], 1
        )
        runtime._resolve_failure(failed)
        snapshot = runtime.snapshot_metrics()
        self.assertEqual(snapshot["deterministic_active_failure_count"], 0)
        self.assertEqual(snapshot["deterministic_worker_error_status"], "resolved")


class CanonicalTextRuntimeLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_stop_defers_queued_canonical_work_for_reconciliation(self) -> None:
        runtime = CanonicalTextRuntime(
            client=mock.Mock(),
            database="q_live",
            live_news=mock.Mock(),
        )
        notice = TextDocumentNotice(
            corpus="news",
            source_id="news-id",
            source_timestamp="2026-07-31T13:45:00Z",
        )
        runtime.enqueue(notice)

        await runtime.stop()

        self.assertEqual(runtime.queue.qsize(), 0)
        self.assertNotIn(("news", "news-id"), runtime.pending)
        self.assertEqual(runtime.metrics["deterministic_shutdown_deferred"], 1)

    def test_reconciliation_avoids_full_sec_rendered_join(self) -> None:
        class Client:
            sql = ""

            def iter_json_each_row(self, sql):
                self.sql = sql
                return iter(
                    (
                        {
                            "corpus": "sec",
                            "source_id": "0000000001-26-000001",
                            "source_timestamp": "2026-07-31 13:45:00",
                            "source_cik": "0000000001",
                        },
                    )
                )

        client = Client()
        runtime = CanonicalTextRuntime(
            client=client,
            database="q_live",
            live_news=mock.Mock(),
        )

        notices = runtime._recent_notices()

        self.assertEqual(notices[0].source_cik, "0000000001")
        self.assertNotIn("sec_filing_text_rendered_v3", client.sql)
        self.assertIn("PREWHERE published_date >=", client.sql)
        self.assertIn("PREWHERE _partition_id >=", client.sql)
        self.assertIn("WHERE published_at_utc >=", client.sql)
        self.assertIn("f.source_updated_at_utc > s.updated_at_utc", client.sql)
        self.assertIn("max_execution_time=25", client.sql)


if __name__ == "__main__":
    unittest.main()
