from __future__ import annotations

import unittest
import asyncio
from unittest import mock

from .scoped_live import ScopedTextRuntime, TextDocumentNotice


class ScopedTextRuntimeTests(unittest.TestCase):
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

        runtime = ScopedTextRuntime(
            client=Client(),
            database="q_live",
            live_news=mock.Mock(),
        )
        with (
            mock.patch(
                "text_intelligence.scoped_live.extend_sec_ticker_mappings"
            ),
            mock.patch(
                "text_intelligence.scoped_live."
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
        runtime = ScopedTextRuntime(
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
        runtime = ScopedTextRuntime(
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
        runtime = ScopedTextRuntime(
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
        runtime._load_sec(
            TextDocumentNotice(
                corpus="sec",
                source_id="0000000001-26-000001",
                source_cik="0000000001",
            )
        )

        self.assertIn("PREWHERE cik='0000000001'", client.sql)
        self.assertIn("accession_number='0000000001-26-000001'", client.sql)
        self.assertIn("max_execution_time=25", client.sql)

    def test_active_failures_clear_only_when_same_source_recovers(self) -> None:
        runtime = ScopedTextRuntime(
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


class ScopedTextRuntimeLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_stop_defers_queued_canonical_work_for_reconciliation(self) -> None:
        runtime = ScopedTextRuntime(
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
        runtime = ScopedTextRuntime(
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
