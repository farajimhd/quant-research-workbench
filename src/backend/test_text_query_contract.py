from __future__ import annotations

import unittest

from src.backend.text_query_contract import TextQuerySessionStore, resolve_text_query_window


class TextQueryContractTests(unittest.TestCase):
    def test_custom_market_dates_are_point_in_time_and_timezone_aware(self) -> None:
        window = resolve_text_query_window(
            as_of="2026-07-10T15:00:00Z",
            lookback_hours=6,
            start_date="2026-07-08",
            end_date="2026-07-10",
        )
        self.assertEqual(window.start.isoformat(), "2026-07-08T04:00:00+00:00")
        self.assertEqual(window.end.isoformat(), "2026-07-10T15:00:00+00:00")
        self.assertTrue(window.custom)

    def test_custom_range_rejects_partial_or_future_windows(self) -> None:
        with self.assertRaisesRegex(ValueError, "Both start_date"):
            resolve_text_query_window(as_of="2026-07-10T15:00:00Z", lookback_hours=6, start_date="2026-07-08")
        with self.assertRaisesRegex(ValueError, "after the active Canvas clock"):
            resolve_text_query_window(
                as_of="2026-07-10T15:00:00Z",
                lookback_hours=6,
                start_date="2026-07-11",
                end_date="2026-07-12",
            )

    def test_query_session_reuses_specs_and_retains_detail_hints(self) -> None:
        store = TextQuerySessionStore(max_sessions=2, ttl_seconds=60)
        first = store.create("news", {"ticker": "AAPL", "limit": 50})
        self.assertEqual(first, store.create("news", {"limit": 50, "ticker": "AAPL"}))
        store.remember(first, "news", {"news-1": {"published_at_utc": "2026-07-10T14:00:00Z"}})
        self.assertEqual(store.hint(first, "news", "news-1")["published_at_utc"], "2026-07-10T14:00:00Z")
        self.assertIsNone(store.get(first, "sec"))


if __name__ == "__main__":
    unittest.main()
