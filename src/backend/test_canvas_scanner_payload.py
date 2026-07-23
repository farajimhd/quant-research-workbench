from datetime import UTC, datetime
import unittest
from unittest.mock import patch

from src.backend.canvas_preview_service import (
    _enrich_scanner_intelligence,
    _merge_scanner_intelligence,
    _query_news,
    _query_scanner_news_intelligence,
    _query_scanner_sec_intelligence,
    scanner_snapshot_payload,
)


class CanvasScannerPayloadTest(unittest.TestCase):
    def test_reference_fields_merge_and_publish_coverage(self) -> None:
        as_of = datetime(2026, 7, 17, 13, 45, tzinfo=UTC)
        snapshot = ([{"symbol": "AAPL", "last": 200.0, "change_pct": 1.0, "change_5m_pct": 0.5}], {"row_count": 1})
        projection = {
            "AAPL": {
                "company_name": "APPLE INC",
                "country": "US",
                "logo_url": "/api/real-live-trading/logo?path=branding%2Flogo%2Faapl.svg",
                "market_cap": 4_374_000_000_000,
                "shares_outstanding": 14_687_000_000,
                "float_shares": 14_400_000_000,
                "short_interest": 144_248_000,
                "short_crowding_pct": 1.0017,
                "days_to_cover": 2.76,
            }
        }
        fundamentals = {
            "AAPL": {
                "xbrl_quality_score": 78.0,
                "xbrl_profitability_score": 95.0,
                "fundamental_operating_margin_pct": 32.0,
                "fundamental_revenue": 416_160_000_000,
            }
        }
        with (
            patch("src.backend.canvas_preview_service.historical_scanner_snapshot", return_value=snapshot),
            patch("src.backend.canvas_preview_service.historical_scanner_reference_projection", return_value=projection),
            patch("src.backend.canvas_preview_service.historical_scanner_fundamental_projection", return_value=fundamentals),
            patch("src.backend.canvas_preview_service._query_scanner_news_intelligence", return_value=[]),
            patch("src.backend.canvas_preview_service._query_scanner_sec_intelligence", return_value=[]),
        ):
            payload = scanner_snapshot_payload(as_of=as_of)

        row = payload["rows"][0]
        self.assertEqual(row["company_name"], "APPLE INC")
        self.assertEqual(row["float_shares"], 14_400_000_000)
        self.assertEqual(row["logo_url"], "/api/real-live-trading/logo?path=branding%2Flogo%2Faapl.svg")
        self.assertEqual(row["xbrl_quality_score"], 78.0)
        self.assertEqual(row["fundamental_operating_margin_pct"], 32.0)
        self.assertEqual(row["live_news_recency"], "none")
        self.assertEqual(row["sec_recency"], "none")
        self.assertEqual(payload["meta"]["field_coverage"]["company_name"], 100.0)
        self.assertEqual(payload["meta"]["field_coverage"]["exchange"], 0.0)
        self.assertEqual(payload["meta"]["field_coverage"]["xbrl_quality_score"], 100.0)
        self.assertEqual(payload["errors"], {})

    def test_company_news_and_sec_labels_are_enriched_separately(self) -> None:
        as_of = datetime(2026, 7, 17, 13, 45, tzinfo=UTC)
        rows = [{"symbol": "AAPL"}]
        news = [
            {
                "is_company_news": True,
                "news_topics": ["earnings", "guidance"],
                "published_at_utc": "2026-07-17T12:30:00Z",
                "tickers": ["AAPL"],
            },
            {
                "is_company_news": False,
                "news_topics": ["market"],
                "published_at_utc": "2026-07-17T13:30:00Z",
                "tickers": ["AAPL"],
            },
            {
                "is_company_news": "0",
                "news_topics": ["analyst"],
                "published_at_utc": "2026-07-17T13:40:00Z",
                "tickers": ["AAPL"],
            },
        ]
        sec = [{"accepted_at_utc": "2026-07-17T11:00:00Z", "form_type": "8-K", "ticker": "AAPL"}]

        _enrich_scanner_intelligence(rows, news, sec, as_of)

        self.assertEqual(rows[0]["live_news_count"], 1)
        self.assertEqual(rows[0]["live_news_recency"], "hot")
        self.assertEqual(rows[0]["news_labels"], "earnings, guidance")
        self.assertEqual(rows[0]["sec_recency"], "hot")
        self.assertEqual(rows[0]["sec_labels"], "8-K")

    def test_news_query_requests_company_classification_and_topics(self) -> None:
        with patch("src.backend.canvas_preview_service._clickhouse_rows", return_value=[]) as clickhouse:
            _query_news(datetime(2026, 7, 17, 13, 45, tzinfo=UTC))

        sql = clickhouse.call_args.args[0]
        self.assertIn("AS is_company_news", sql)
        self.assertIn("AS news_topics", sql)
        self.assertIn("provider_tags", sql)

    def test_scanner_intelligence_queries_aggregate_by_ticker_without_preview_limits(self) -> None:
        as_of = datetime(2026, 7, 17, 13, 45, tzinfo=UTC)
        with patch("src.backend.canvas_preview_service._clickhouse_rows", return_value=[]) as clickhouse:
            _query_scanner_news_intelligence(as_of)
            news_sql = clickhouse.call_args.args[0]
            _query_scanner_sec_intelligence(as_of)
            sec_sql = clickhouse.call_args.args[0]

        self.assertIn("GROUP BY ticker", news_sql)
        self.assertIn("WHERE is_company_news", news_sql)
        self.assertNotIn("LIMIT 30", news_sql)
        self.assertIn("GROUP BY ticker", sec_sql)
        self.assertIn("valid_to_date_exclusive", sec_sql)
        self.assertNotIn("LIMIT 30", sec_sql)

    def test_aggregated_scanner_intelligence_populates_labels_and_recency(self) -> None:
        as_of = datetime(2026, 7, 17, 13, 45, tzinfo=UTC)
        rows = [{"symbol": "AAPL"}, {"symbol": "MSFT"}]
        news = [{"ticker": "AAPL", "live_news_count": 2, "latest_news_at": "2026-07-17T13:15:00Z", "news_labels": ["guidance", "earnings"]}]
        sec = [{"ticker": "AAPL", "sec_count": 1, "latest_sec_at": "2026-07-16T20:00:00Z", "sec_labels": ["8-K"]}]

        _merge_scanner_intelligence(rows, news, sec, as_of)

        self.assertEqual(rows[0]["news_labels"], "earnings, guidance")
        self.assertEqual(rows[0]["live_news_recency"], "hot")
        self.assertEqual(rows[0]["sec_labels"], "8-K")
        self.assertEqual(rows[0]["sec_recency"], "cold")
        self.assertEqual(rows[1]["live_news_recency"], "none")


if __name__ == "__main__":
    unittest.main()
