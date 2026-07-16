from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from src.backend.app import trading_news_rows


class TradingNewsTests(unittest.TestCase):
    @patch("src.backend.app.clickhouse_status_query")
    def test_query_is_point_in_time_filtered_and_paginated(self, query_mock) -> None:
        rows = [
            {"canonical_news_id": "n1", "published_at_utc": "2026-07-10T13:44:00.000000Z", "title": "Apple update"},
            {"canonical_news_id": "n0", "published_at_utc": "2026-07-10T13:43:00.000000Z", "title": "Older"},
        ]
        query_mock.return_value = "\n".join(json.dumps(row) for row in rows)

        payload = trading_news_rows(
            as_of="2026-07-10T13:45:00Z",
            lookback_hours=24,
            limit=1,
            search="Apple",
            ticker="aapl",
            content="full",
        )

        self.assertEqual(payload["rows"][0]["canonical_news_id"], "n1")
        self.assertTrue(payload["has_more"])
        self.assertEqual(payload["next_before"], "2026-07-10T13:44:00.000000Z")
        self.assertEqual(payload["next_before_id"], "n1")
        sql = query_mock.call_args.args[0]
        self.assertIn("has(t.ticker_link_sample, 'AAPL')", sql)
        self.assertIn("positionCaseInsensitiveUTF8", sql)
        self.assertIn("NOT n.is_title_only", sql)
        self.assertIn("n.published_at_utc <= window_end", sql)
        self.assertIn("AS news_kind", sql)
        self.assertIn("'analyst'", sql)
        self.assertIn("'multi'", sql)
        self.assertIn("'company'", sql)
        self.assertIn("LIMIT 2", sql)

    @patch("src.backend.app.clickhouse_status_query", return_value="")
    def test_cursor_keeps_same_timestamp_rows_ordered(self, query_mock) -> None:
        trading_news_rows(
            as_of="2026-07-10T13:45:00Z",
            before="2026-07-10T13:44:00Z",
            before_id="news-002",
        )
        sql = query_mock.call_args.args[0]
        self.assertIn("n.published_at_utc = page_before", sql)
        self.assertIn("n.canonical_news_id < 'news-002'", sql)

    def test_query_rejects_invalid_filters(self) -> None:
        with self.assertRaises(HTTPException):
            trading_news_rows(as_of="not-a-date")
        with self.assertRaises(HTTPException):
            trading_news_rows(ticker="AAPL; DROP")
        with self.assertRaises(HTTPException):
            trading_news_rows(content="summary")


if __name__ == "__main__":
    unittest.main()
