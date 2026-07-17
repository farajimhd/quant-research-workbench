from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from src.backend.app import SERVICE_REGISTRY, service_websocket_url, trading_news_rows


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
        self.assertIn("ticker = 'AAPL'", sql)
        self.assertIn("n.canonical_news_id IN", sql)
        self.assertIn("n.published_date >= toDate(window_start)", sql)
        self.assertIn("arrayMap(value -> upperUTF8(trimBoth(value)), n.tickers)", sql)
        self.assertNotIn("ticker_counts AS", sql)
        self.assertIn("positionCaseInsensitiveUTF8", sql)
        self.assertIn("NOT n.is_title_only", sql)
        self.assertIn("n.published_at_utc <= window_end", sql)
        self.assertIn("AS news_kind", sql)
        self.assertIn("'analyst'", sql)
        self.assertIn("'multi'", sql)
        self.assertIn("'company'", sql)
        self.assertIn("LIMIT 2", sql)
        self.assertEqual(query_mock.call_args.kwargs["timeout_seconds"], 12.0)

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

    @patch.dict("os.environ", {"NEWS_GATEWAY_BIND": "0.0.0.0:8796"})
    def test_news_gateway_websocket_uses_loopback_for_wildcard_bind(self) -> None:
        self.assertEqual(service_websocket_url(SERVICE_REGISTRY["news"], "/stream/news"), "ws://127.0.0.1:8796/stream/news")

    @patch("src.backend.app.clickhouse_status_query", side_effect=TimeoutError("timed out"))
    def test_clickhouse_timeout_is_reported_as_gateway_timeout(self, _query_mock) -> None:
        with self.assertRaises(HTTPException) as raised:
            trading_news_rows(as_of="2026-07-10T13:45:00Z")
        self.assertEqual(raised.exception.status_code, 504)


if __name__ == "__main__":
    unittest.main()
