from __future__ import annotations

import asyncio
import unittest
from unittest import mock

from text_intelligence.sec_review import SecReviewRequest, SecReviewRuntime


class SecReviewAdmissionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.runtime = SecReviewRuntime(mock.Mock(), mock.Mock(), "q_live")
        self.request = SecReviewRequest(
            cik="0001930510",
            accession_number="0001213900-26-092120",
            requested_by="frontend-operator",
        )
        self.synthesis = {"source_hash": "source-1"}

    async def test_admission_is_durable_before_work_becomes_visible(self) -> None:
        events: list[str] = []
        self.runtime._load_synthesis = mock.Mock(return_value=self.synthesis)
        self.runtime._review_complete = mock.Mock(return_value=False)
        self.runtime._write_status = mock.Mock(side_effect=lambda *_args, **_kwargs: events.append("persisted"))

        result = await self.runtime.enqueue(self.request)
        events.append("visible" if self.runtime.queue.qsize() == 1 else "missing")

        self.assertEqual("queued", result["status"])
        self.assertEqual(["persisted", "visible"], events)
        self.assertIn((self.request.cik, self.request.accession_number), self.runtime.pending)

    async def test_duplicate_admission_does_not_enqueue_twice(self) -> None:
        self.runtime._load_synthesis = mock.Mock(return_value=self.synthesis)
        self.runtime._review_complete = mock.Mock(return_value=False)
        self.runtime._write_status = mock.Mock()

        await self.runtime.enqueue(self.request)
        duplicate = await self.runtime.enqueue(self.request)

        self.assertEqual("already_queued", duplicate["status"])
        self.assertEqual(1, self.runtime.queue.qsize())
        self.runtime._write_status.assert_called_once()

    async def test_failed_durable_write_does_not_expose_work(self) -> None:
        self.runtime._load_synthesis = mock.Mock(return_value=self.synthesis)
        self.runtime._review_complete = mock.Mock(return_value=False)
        self.runtime._write_status = mock.Mock(side_effect=TimeoutError("storage timeout"))

        with self.assertRaises(TimeoutError):
            await self.runtime.enqueue(self.request)

        self.assertEqual(0, self.runtime.queue.qsize())
        self.assertNotIn((self.request.cik, self.request.accession_number), self.runtime.pending)

    async def test_full_queue_rejects_before_durable_write(self) -> None:
        self.runtime.queue = asyncio.Queue(maxsize=1)
        self.runtime.queue.put_nowait(mock.Mock())
        self.runtime._load_synthesis = mock.Mock(return_value=self.synthesis)
        self.runtime._review_complete = mock.Mock(return_value=False)
        self.runtime._write_status = mock.Mock()

        with self.assertRaises(asyncio.QueueFull):
            await self.runtime.enqueue(self.request)

        self.runtime._write_status.assert_not_called()


if __name__ == "__main__":
    unittest.main()
