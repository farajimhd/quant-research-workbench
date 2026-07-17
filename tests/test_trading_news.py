from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from src.backend.app import SERVICE_REGISTRY, service_websocket_url, trading_news_detail, trading_news_rows
from src.backend.news_classification import classify_news, classify_news_kind


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
            kind="analyst",
        )

        self.assertEqual(payload["rows"][0]["canonical_news_id"], "n1")
        self.assertTrue(payload["has_more"])
        self.assertEqual(payload["next_before"], "2026-07-10T13:44:00.000000Z")
        self.assertEqual(payload["next_before_id"], "n1")
        self.assertNotIn("source", payload)
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
        self.assertIn("AS news_scope", sql)
        self.assertIn("AS news_origin", sql)
        self.assertIn("AS news_format", sql)
        self.assertIn("AS news_topics", sql)
        self.assertIn("AS is_company_news", sql)
        self.assertIn("'analyst'", sql)
        self.assertIn("'insights'", sql)
        self.assertIn("startsWith(lowerUTF8(trimBoth(value)), 'bzi-')", sql)
        self.assertIn(") = 'analyst'", sql)
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
        with self.assertRaises(HTTPException):
            trading_news_rows(kind="urgent")

    def test_news_kind_classification_is_shared_with_detail_rows(self) -> None:
        self.assertEqual(classify_news_kind({"provider_tags": ["BenzAI"]}, 1), "ai")
        self.assertEqual(classify_news_kind({"channels": ["Price Target"]}, 1), "analyst")
        self.assertEqual(classify_news_kind({"author": "Benzinga Insights", "provider_tags": ["bzi-ia"]}, 1), "insights")
        self.assertEqual(classify_news_kind({"channels": ["Analyst Ratings"], "provider_tags": ["bzi-ratings"]}, 1), "analyst")
        self.assertEqual(classify_news_kind({}, 2), "multi")
        self.assertEqual(classify_news_kind({}, 1), "market")
        self.assertEqual(classify_news_kind({}, 0), "market")

    def test_single_ticker_editorial_story_is_not_company_news(self) -> None:
        classification = classify_news(
            {
                "author": "Vandana Singh",
                "channels": ["trading ideas", "movers", "news", "earnings", "guidance", "general", "top stories", "health care", "large cap"],
                "provider_tags": ["why it's moving"],
                "title": "HCA Healthcare Trims 2026 Profit Outlook, Stock Falls",
            },
            1,
        )

        self.assertEqual(classification.kind, "why_moving")
        self.assertEqual(classification.origin, "editorial")
        self.assertEqual(classification.format, "why_moving")
        self.assertFalse(classification.is_company_news)
        self.assertIn("earnings", classification.topics)
        self.assertIn("guidance", classification.topics)

    def test_direct_issuer_release_is_company_news(self) -> None:
        classification = classify_news(
            {
                "channels": ["contracts"],
                "links": ["https://www.businesswire.com/news/home/example"],
                "text": "The company announced today that it was awarded a new contract.",
                "title": "Acme wins new contract",
            },
            1,
        )

        self.assertEqual(classification.kind, "company")
        self.assertEqual(classification.origin, "issuer")
        self.assertTrue(classification.is_company_news)

    @patch("src.backend.app.clickhouse_status_query")
    def test_trading_detail_contract_excludes_internal_implementation_fields(self, query_mock) -> None:
        query_mock.side_effect = [
            json.dumps({
                "canonical_news_id": "b2185e66008f39d6875a8f4449f82b7f",
                "title": "Insights Into Apple's Performance",
                "text": "Readable article body.",
                "article_url": "https://example.test/article",
                "url_domain": "example.test",
                "author": "Benzinga Insights",
                "channels": ["news", "markets"],
                "links": [],
                "provider_tags": ["bzi-ia"],
                "published_at_utc": "2026-07-14T09:44:00.000000Z",
                "downloaded_at_utc": "2026-07-14T09:58:50.653569Z",
                "raw_artifact_path": "C:/private/raw.json",
            }),
            json.dumps({"ticker": "AAPL", "canonical_news_id": "b2185e66008f39d6875a8f4449f82b7f"}),
        ]

        payload = trading_news_detail("b2185e66008f39d6875a8f4449f82b7f")

        self.assertEqual(payload["article"]["news_kind"], "insights")
        self.assertEqual(payload["article"]["classification"]["origin"], "automated")
        self.assertEqual(payload["article"]["text"], "Readable article body.")
        self.assertEqual(payload["tickers"], ["AAPL"])
        serialized = json.dumps(payload)
        for forbidden in ("database", "normalized_table", "ticker_table", "raw_artifact_path", "downloaded_at_utc", "C:/private"):
            self.assertNotIn(forbidden, serialized)

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
