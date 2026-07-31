from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path


SERVICE_ROOT = (
    Path(__file__).resolve().parents[1] / "services" / "text-intelligence"
)
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from text_intelligence.live import (  # noqa: E402
    LiveCandidate,
    LiveNewsRuntime,
    PreparedNewsCandidate,
)
from text_intelligence.scoped_live import (  # noqa: E402
    _parse_utc,
    _sec_document_set_hash,
)


class ScopedTextLiveRuntimeTests(unittest.TestCase):
    def test_sec_hash_is_order_independent_and_revision_sensitive(self) -> None:
        left = [
            {"source_id": "doc-b", "text_sha256": "bbb"},
            {"source_id": "doc-a", "text_sha256": "aaa"},
        ]
        right = list(reversed(left))
        changed = [
            {"source_id": "doc-a", "text_sha256": "aaa"},
            {"source_id": "doc-b", "text_sha256": "changed"},
        ]

        self.assertEqual(
            _sec_document_set_hash(left), _sec_document_set_hash(right)
        )
        self.assertNotEqual(
            _sec_document_set_hash(left), _sec_document_set_hash(changed)
        )

    def test_threadsafe_forward_reports_queue_acceptance(self) -> None:
        async def exercise() -> None:
            runtime = LiveNewsRuntime(enabled=True)
            runtime.loop = asyncio.get_running_loop()
            candidate = LiveCandidate(
                canonical_news_id="article-1",
                published_at_utc="2026-07-28T14:30:00Z",
                title="Example",
                rendered_text="Example body",
            )
            item = PreparedNewsCandidate(candidate=candidate, scoped_labels=())
            try:
                accepted = await asyncio.to_thread(
                    runtime.enqueue_prepared_threadsafe, item
                )
                self.assertTrue(accepted)
                self.assertEqual(runtime.queue.qsize(), 1)
                self.assertIn("article-1", runtime.pending_ids)
            finally:
                runtime.client.close()

        asyncio.run(exercise())

    def test_disabled_live_ai_rejects_model_work(self) -> None:
        runtime = LiveNewsRuntime()
        candidate = LiveCandidate(
            canonical_news_id="article-disabled",
            published_at_utc="2026-07-28T14:30:00Z",
            title="Example",
            rendered_text="Example body",
        )
        item = PreparedNewsCandidate(candidate=candidate, scoped_labels=())
        try:
            self.assertFalse(runtime.enqueue_prepared(item))
            self.assertEqual(runtime.queue.qsize(), 0)
            self.assertEqual(runtime.metrics["enabled"], 0)
        finally:
            runtime.client.close()

    def test_disabled_live_ai_starts_without_optional_workers(self) -> None:
        async def exercise() -> None:
            runtime = LiveNewsRuntime()
            await runtime.start()
            try:
                self.assertEqual(runtime.workers, [])
                self.assertIsNone(runtime.session_sync_task)
            finally:
                await runtime.stop()

        asyncio.run(exercise())

    def test_utc_parser_normalizes_naive_and_zulu_values(self) -> None:
        naive = _parse_utc("2026-07-28T14:30:00")
        zulu = _parse_utc("2026-07-28T14:30:00Z")
        self.assertIsNotNone(naive)
        self.assertEqual(naive, zulu)
        self.assertIsNone(_parse_utc("not-a-time"))


if __name__ == "__main__":
    unittest.main()
