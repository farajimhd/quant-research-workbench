from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from src.backend.app import SERVICE_REGISTRY, service_websocket_url, trading_news_detail, trading_news_detail_route, trading_news_rows


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
        sql = query_mock.call_args_list[0].args[0]
        self.assertIn("has(n.tickers, 'AAPL')", sql)
        self.assertIn("benzinga_news_rendered_v2", sql)
        self.assertIn("r.source_revision_key=n.source_revision_key", sql)
        self.assertIn("n.published_date >= toDate(window_start)", sql)
        self.assertIn("PREWHERE published_date >= toDate('2026-07-09')", sql)
        self.assertEqual(sql.count("FROM `q_live`.`benzinga_news_event_v2` FINAL"), 1)
        self.assertEqual(sql.count("FROM `q_live`.`benzinga_news_rendered_v2` FINAL"), 1)
        self.assertIn("arrayMap(value -> upperUTF8(trimBoth(value)), n.tickers)", sql)
        self.assertNotIn("ticker_counts AS", sql)
        self.assertIn("positionCaseInsensitiveUTF8", sql)
        self.assertIn("ifNull(n.canonical_news_id, '')", sql)
        self.assertIn("ifNull(n.provider_article_id, '')", sql)
        self.assertIn("arrayStringConcat(n.tickers, ' ')", sql)
        self.assertIn("ifNull(r.source_count, 0) > 0", sql)
        self.assertIn("n.published_at_utc <= window_end", sql)
        self.assertIn("AS news_kind", sql)
        self.assertIn("AS news_scope", sql)
        self.assertIn("AS news_origin", sql)
        self.assertIn("AS news_format", sql)
        self.assertIn("AS news_topics", sql)
        self.assertIn("AS is_company_news", sql)
        self.assertIn("'analyst'", sql)
        self.assertIn("q_live.news_synthesis_v1", sql)
        self.assertIn("l.information_origin='analyst'", sql)
        self.assertIn("LIMIT 2", sql)
        self.assertEqual(query_mock.call_args_list[0].kwargs["timeout_seconds"], 12.0)
        facet_sql = query_mock.call_args_list[1].args[0]
        self.assertNotIn("page_before", facet_sql)
        self.assertNotIn("has(n.tickers, 'AAPL')", facet_sql)
        self.assertIn("news_synthesis_v1", query_mock.call_args_list[2].args[0])
        self.assertIn("engine_version", query_mock.call_args_list[2].args[0])
        self.assertTrue(payload["query_id"])
        self.assertEqual(payload["market_timezone"], "America/New_York")
        self.assertEqual(query_mock.call_args_list[2].kwargs["timeout_seconds"], 1.5)

    @patch("src.backend.app.clickhouse_status_query", return_value="")
    def test_search_includes_exact_source_identity(self, query_mock) -> None:
        source_id = "d99dd26da27e325682cb6be4274d3b60"

        trading_news_rows(
            as_of="2026-08-02T12:00:00Z",
            start_date="2017-12-15",
            end_date="2017-12-15",
            search=source_id,
        )

        sql = query_mock.call_args_list[0].args[0]
        self.assertEqual(sql.count(f"canonical_news_id = '{source_id}'"), 3)
        self.assertNotIn("ifNull(n.canonical_news_id, ''), ' '", sql)

    @patch("src.backend.app.clickhouse_status_query")
    def test_exact_source_identity_is_not_hidden_by_full_text_filter(self, query_mock) -> None:
        source_id = "d99dd26da27e325682cb6be4274d3b60"
        query_mock.side_effect = [
            json.dumps({"canonical_news_id": source_id, "published_at_utc": "2017-12-15T15:52:09.000000Z", "is_title_only": 1}),
            json.dumps({"ticker_options": ["AYTU"]}),
            "",
        ]

        payload = trading_news_rows(
            as_of="2017-12-16T04:59:59Z",
            start_date="2017-12-15",
            end_date="2017-12-15",
            search=source_id,
            content="full",
        )

        self.assertEqual(payload["rows"][0]["canonical_news_id"], source_id)
        self.assertEqual(payload["ticker_options"], ["AYTU"])
        self.assertNotIn("ifNull(r.source_count, 0) > 0", query_mock.call_args_list[0].args[0])
        self.assertNotIn("ifNull(r.source_count, 0) > 0", query_mock.call_args_list[1].args[0])

    @patch("src.backend.app.clickhouse_status_query")
    def test_exact_source_identity_bypasses_window_and_toolbar_refinements(self, query_mock) -> None:
        source_id = "d99dd26da27e325682cb6be4274d3b60"
        query_mock.side_effect = [
            json.dumps({"canonical_news_id": source_id, "published_at_utc": "2017-12-15T15:52:09.000000Z", "is_title_only": 1}),
            json.dumps({"ticker_options": ["AYTU", "NDAQ"]}),
            "",
        ]

        payload = trading_news_rows(
            as_of="2026-07-31T13:45:00Z",
            lookback_hours=6,
            search=source_id,
            ticker="AAPL",
            content="full",
            kind="analyst",
            role="primary_event",
            origin="issuer",
            direction="negative",
            forecast_eligible="ineligible",
            label_state="pending",
        )

        self.assertEqual(payload["rows"][0]["canonical_news_id"], source_id)
        self.assertTrue(payload["window_start"].startswith("2016-"))
        main_sql = query_mock.call_args_list[0].args[0]
        self.assertNotIn("has(n.tickers, 'AAPL')", main_sql)
        self.assertNotIn("l.ticker = 'AAPL'", main_sql)
        self.assertNotIn("ifNull(r.source_count, 0) > 0", main_sql)
        self.assertNotIn("countIf(l.content_role = 'primary_event')", main_sql)

    @patch("src.backend.app.clickhouse_status_query")
    def test_ticker_options_cover_full_filtered_query_not_page_or_ticker(self, query_mock) -> None:
        query_mock.side_effect = [
            json.dumps({"canonical_news_id": "page-row", "published_at_utc": "2026-07-10T13:44:00.000000Z", "ticker_link_sample": ["AAPL"]}),
            json.dumps({"ticker_options": ["AAPL", "MSFT", "NVDA"]}),
            "",
        ]

        payload = trading_news_rows(
            as_of="2026-07-10T13:45:07Z",
            lookback_hours=24,
            search="earnings",
            ticker="AAPL",
        )

        self.assertEqual(payload["ticker_options"], ["AAPL", "MSFT", "NVDA"])
        main_sql, facet_sql = [call.args[0] for call in query_mock.call_args_list[:2]]
        self.assertIn("has(n.tickers, 'AAPL')", main_sql)
        self.assertNotIn("has(n.tickers, 'AAPL')", facet_sql)
        self.assertNotIn("page_before", facet_sql)
        self.assertIn("positionCaseInsensitiveUTF8", facet_sql)

    @patch("src.backend.app.clickhouse_status_query")
    def test_ticker_options_are_transferred_only_on_initial_query_page(self, query_mock) -> None:
        query_mock.side_effect = [
            "\n".join([
                json.dumps({"canonical_news_id": "facet-page-2", "published_at_utc": "2026-07-10T13:44:00.000000Z"}),
                json.dumps({"canonical_news_id": "facet-page-1", "published_at_utc": "2026-07-10T13:43:00.000000Z"}),
            ]),
            json.dumps({"ticker_options": ["AAPL", "MSFT", "NVDA"]}),
            "",
            json.dumps({"canonical_news_id": "facet-page-1", "published_at_utc": "2026-07-10T13:43:00.000000Z"}),
            "",
        ]

        first = trading_news_rows(
            as_of="2026-07-10T13:45:07Z",
            limit=1,
            search="once-per-query-ticker-facet",
        )
        second = trading_news_rows(
            as_of="2026-07-10T13:45:07Z",
            before=first["next_before"],
            before_id=first["next_before_id"],
            limit=1,
            query_id=first["query_id"],
            search="once-per-query-ticker-facet",
        )

        self.assertEqual(first["ticker_options"], ["AAPL", "MSFT", "NVDA"])
        self.assertNotIn("ticker_options", second)
        self.assertEqual(query_mock.call_count, 5)
        self.assertNotIn("groupUniqArray(ticker)", query_mock.call_args_list[3].args[0])

    @patch("src.backend.app.clickhouse_status_query", return_value="")
    def test_custom_date_range_is_bounded_by_market_dates(self, query_mock) -> None:
        payload = trading_news_rows(
            as_of="2026-07-10T15:00:00Z",
            start_date="2026-07-08",
            end_date="2026-07-10",
            limit=25,
        )
        sql = query_mock.call_args_list[0].args[0]
        self.assertIn("PREWHERE published_date >= toDate('2026-07-08')", sql)
        self.assertIn("published_at_utc <= toDateTime64('2026-07-10 15:00:00.000000'", sql)
        self.assertEqual(payload["limit"], 25)
        self.assertEqual(payload["market_timezone"], "America/New_York")
        self.assertEqual(payload["window_start"], "2026-07-08T04:00:00Z")

    @patch("src.backend.app.clickhouse_status_query", return_value="")
    def test_cursor_keeps_same_timestamp_rows_ordered(self, query_mock) -> None:
        trading_news_rows(
            as_of="2026-07-10T13:45:00Z",
            before="2026-07-10T13:44:00Z",
            before_id="news-002",
        )
        sql = query_mock.call_args_list[0].args[0]
        self.assertIn("n.published_at_utc = page_before", sql)
        self.assertIn("n.canonical_news_id < 'news-002'", sql)
        self.assertIn("published_at_utc = page_before", sql)
        self.assertIn("canonical_news_id < 'news-002'", sql)

    @patch("src.backend.app.clickhouse_status_query", return_value="")
    def test_each_v1_eligibility_filter_queries_aggregate_state_before_limit(self, query_mock) -> None:
        trading_news_rows(
            as_of="2026-07-10T13:45:00Z",
            forecast_eligible="eligible",
            reaction_eligible="ineligible",
            history_eligible="eligible",
        )
        sql = query_mock.call_args_list[0].args[0]
        self.assertIn("notEmpty(l.forecast_tickers)", sql)
        self.assertIn("NOT (notEmpty(l.reaction_tickers))", sql)
        self.assertIn("notEmpty(l.history_tickers)", sql)
        self.assertLess(sql.index("news_synthesis_v1"), sql.index("LIMIT 101"))

    @patch("src.backend.app.clickhouse_status_query", return_value="")
    def test_unfiltered_ticker_query_limits_events_before_rendered_join(self, query_mock) -> None:
        trading_news_rows(
            as_of="2026-07-10T13:45:00Z",
            lookback_hours=72,
            limit=100,
            ticker="AAPL",
        )
        sql = query_mock.call_args_list[0].args[0]
        event_source, rendered_join = sql.split("LEFT JOIN", 1)
        self.assertIn("AND has(tickers, 'AAPL')", event_source)
        self.assertIn("ORDER BY published_at_utc DESC, canonical_news_id DESC LIMIT 101", event_source)
        self.assertNotIn("LIMIT 101", rendered_join.split("ORDER BY n.published_at_utc", 1)[0])

    def test_query_rejects_invalid_filters(self) -> None:
        with self.assertRaises(HTTPException):
            trading_news_rows(as_of="not-a-date")
        with self.assertRaises(HTTPException):
            trading_news_rows(ticker="AAPL; DROP")
        with self.assertRaises(HTTPException):
            trading_news_rows(content="summary")
        with self.assertRaises(HTTPException):
            trading_news_rows(kind="urgent")
        with self.assertRaises(HTTPException):
            trading_news_rows(forecast_eligible="maybe")

    @patch("src.backend.app.clickhouse_status_query")
    def test_trading_detail_contract_excludes_internal_implementation_fields(self, query_mock) -> None:
        query_mock.side_effect = [
            json.dumps({
                "canonical_news_id": "b2185e66008f39d6875a8f4449f82b7f",
                "provider_article_id": "12345",
                "published_date": "2026-07-14",
                "source_revision_key": "revision-1",
                "title": "Insights Into Apple's Performance",
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
            json.dumps({"text": "Readable article body.", "render_status": "rendered"}),
            json.dumps({"ticker": "AAPL", "canonical_news_id": "b2185e66008f39d6875a8f4449f82b7f"}),
            "",
        ]

        payload = trading_news_detail("b2185e66008f39d6875a8f4449f82b7f")

        self.assertEqual(payload["article"]["news_kind"], "market")
        self.assertEqual(payload["article"]["classification"]["origin"], "unknown")
        self.assertEqual(payload["article"]["intelligence_status"], "pending")
        self.assertEqual(payload["article"]["text"], "Readable article body.")
        self.assertEqual(payload["tickers"], ["AAPL"])
        event_sql, render_sql, ticker_sql = [call.args[0] for call in query_mock.call_args_list[:3]]
        self.assertNotIn("benzinga_news_rendered_v2", event_sql)
        self.assertIn("PREWHERE published_date = toDate('2026-07-14')", render_sql)
        self.assertIn("PREWHERE t.published_date = toDate('2026-07-14')", ticker_sql)
        self.assertNotIn("benzinga_news_event_v2", ticker_sql)
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

    @patch("src.backend.app.trading_news_detail", side_effect=TimeoutError("partial response"))
    def test_detail_timeout_is_reported_as_recoverable_service_failure(self, _detail_mock) -> None:
        with self.assertRaises(HTTPException) as raised:
            trading_news_detail_route("news-1")
        self.assertEqual(raised.exception.status_code, 503)
        self.assertIn("Reopen it from All News", str(raised.exception.detail))


if __name__ == "__main__":
    unittest.main()
