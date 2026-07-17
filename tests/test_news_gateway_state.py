from __future__ import annotations

import unittest

from services.news_gateway.state import NewsMemoryState


class NewsGatewayStateTests(unittest.IsolatedAsyncioTestCase):
    async def test_snapshots_expose_monotonic_revision_for_live_invalidation(self) -> None:
        state = NewsMemoryState(100)
        initial = await state.recent_snapshot()

        await state.upsert_rows(
            [
                {
                    "canonical_news_id": "news-1",
                    "published_at_utc": "2026-07-17T13:45:00Z",
                    "title": "Initial normalized row",
                    "tickers": ["AAPL"],
                }
            ]
        )
        pending = await state.recent_snapshot()

        await state.upsert_rows(
            [
                {
                    "canonical_news_id": "news-1",
                    "published_at_utc": "2026-07-17T13:45:00Z",
                    "title": "Enriched durable row",
                    "tickers": ["AAPL"],
                    "has_body": 1,
                }
            ]
        )
        durable = await state.ticker_snapshot("AAPL")

        self.assertEqual(initial["revision"], 0)
        self.assertEqual(pending["revision"], 1)
        self.assertEqual(durable["revision"], 2)
        self.assertEqual(durable["rows"][0]["title"], "Enriched durable row")


if __name__ == "__main__":
    unittest.main()
