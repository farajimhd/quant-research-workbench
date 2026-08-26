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
    def test_core_scope_returns_market_rows_without_waiting_for_enrichment(self) -> None:
        as_of = datetime(2026, 7, 17, 13, 45, tzinfo=UTC)
        snapshot = (
            [{"symbol": "AAPL", "last": 200.0, "change_pct": 1.0}],
            {
                "complete_universe": True,
                "row_count": 1,
                "snapshot_at_utc": as_of.isoformat(),
                "status": "ready",
            },
        )
        with (
            patch("src.backend.canvas_preview_service.historical_scanner_snapshot", return_value=snapshot),
            patch("src.backend.trading_configuration_service.configuration_base", return_value={"market_discovery": {}}),
            patch(
                "src.backend.canvas_preview_service.historical_scanner_reference_projection",
                return_value={"AAPL": {"company_name": "APPLE INC", "market_cap": 4_000_000_000_000}},
            ) as reference,
            patch("src.backend.canvas_preview_service.historical_scanner_fundamental_projection") as fundamentals,
            patch("src.backend.canvas_preview_service._query_scanner_news_intelligence") as news,
            patch("src.backend.canvas_preview_service._query_scanner_sec_intelligence") as sec,
            patch("src.backend.canvas_preview_service.historical_scanner_qmd_projection_or_schedule") as qmd,
            patch(
                "src.backend.watchlist_runtime_service.project_watchlists_from_candidates",
                return_value={"status": "ready", "watchlists": []},
            ) as watchlists,
        ):
            payload = scanner_snapshot_payload(as_of=as_of, enrichment_scope="core")

        reference.assert_not_called()
        fundamentals.assert_not_called()
        news.assert_not_called()
        sec.assert_not_called()
        qmd.assert_not_called()
        self.assertEqual(payload["rows"][0]["symbol"], "AAPL")
        self.assertEqual(payload["meta"]["enrichment_scope"], "core")
        self.assertEqual(payload["meta"]["included_enrichments"], [])
        watchlists.assert_not_called()
        self.assertEqual(payload["watchlist_runtime"]["status"], "not_requested")

    def test_empty_snapshot_skips_every_enrichment_branch(self) -> None:
        as_of = datetime(2026, 7, 17, 13, 45, tzinfo=UTC)
        snapshot = (
            [],
            {
                "complete_universe": False,
                "row_count": 0,
                "snapshot_at_utc": as_of.isoformat(),
                "status": "building",
            },
        )
        with (
            patch("src.backend.canvas_preview_service.historical_scanner_snapshot", return_value=snapshot),
            patch("src.backend.trading_configuration_service.configuration_base", return_value={"market_discovery": {}}),
            patch("src.backend.canvas_preview_service.historical_scanner_fundamental_projection") as fundamentals,
            patch("src.backend.canvas_preview_service.historical_scanner_reference_projection") as reference,
            patch("src.backend.canvas_preview_service._query_scanner_news_intelligence") as news,
            patch("src.backend.canvas_preview_service._query_scanner_sec_intelligence") as sec,
            patch("src.backend.canvas_preview_service.historical_scanner_qmd_projection_or_schedule") as qmd,
            patch(
                "src.backend.watchlist_runtime_service.project_watchlists_from_candidates",
                return_value={"status": "building", "watchlists": []},
            ),
        ):
            payload = scanner_snapshot_payload(as_of=as_of)

        for enrichment in (fundamentals, reference, news, sec, qmd):
            enrichment.assert_not_called()
        self.assertEqual(payload["rows"], [])
        self.assertEqual(payload["meta"]["included_enrichments"], [])

    def test_page_enrichment_does_not_materialize_discovery_runtime(self) -> None:
        as_of = datetime(2026, 7, 17, 13, 45, tzinfo=UTC)
        snapshot = (
            [
                {"symbol": "LOW", "last": 10.0, "change_5m_pct": 1.0},
                {"symbol": "HIGH", "last": 20.0, "change_5m_pct": 5.0},
            ],
            {"complete_universe": True, "snapshot_at_utc": as_of.isoformat(), "status": "ready"},
        )
        with (
            patch("src.backend.canvas_preview_service.historical_scanner_snapshot", return_value=snapshot),
            patch("src.backend.canvas_preview_service.historical_scanner_reference_projection", return_value={}),
            patch("src.backend.canvas_preview_service.historical_scanner_fundamental_projection", return_value={}),
            patch("src.backend.canvas_preview_service._query_scanner_news_intelligence", return_value=[]),
            patch("src.backend.canvas_preview_service._query_scanner_sec_intelligence", return_value=[]),
            patch(
                "src.backend.canvas_preview_service.historical_scanner_qmd_projection_or_schedule",
                return_value=({}, [], {"qmd_derived_status": "ready"}),
            ),
            patch("src.backend.watchlist_runtime_service.project_watchlists_from_candidates") as watchlists,
        ):
            payload = scanner_snapshot_payload(
                as_of=as_of,
                enrichment_scope="full",
                materialize_discovery=False,
                row_limit=1,
            )

        self.assertEqual([row["symbol"] for row in payload["rows"]], ["HIGH"])
        self.assertEqual(payload["meta"]["total_row_count"], 2)
        self.assertEqual(payload["watchlist_runtime"]["status"], "not_requested")
        watchlists.assert_not_called()

    def test_reference_fields_merge_and_publish_coverage(self) -> None:
        as_of = datetime(2026, 7, 17, 13, 45, tzinfo=UTC)
        snapshot = (
            [{"symbol": "AAPL", "last": 200.0, "change_pct": 1.0, "change_5m_pct": 0.5}],
            {"row_count": 1, "snapshot_at_utc": "2026-07-17T13:44:00+00:00", "status": "refreshing"},
        )
        projection = {
            "AAPL": {
                "company_name": "APPLE INC",
                "country": "US",
                "logo_url": "/api/real-live-trading/logo?path=branding%2Flogo%2Faapl.svg",
                "market_cap": 4_374_000_000_000,
                "shares_outstanding": 14_687_000_000,
                "float_shares": 14_400_000_000,
                "float_source": "massive",
                "float_quality": "reported",
                "short_pressure": "moderate",
                "short_interest": 144_248_000,
                "short_crowding_pct": 1.0017,
                "short_interest_pct": 1.0017,
                "days_to_cover": 2.76,
                "short_volume": 12_000_000,
                "short_volume_pct": 41.2,
                "fails_to_deliver": 120_000,
                "ftd_value": 24_000_000,
                "reg_sho_threshold": True,
                "borrow_status": "shortable",
                "borrow_shares": 3_000_000,
                "borrow_fee": 0.25,
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
            patch(
                "src.backend.canvas_preview_service.historical_scanner_qmd_projection_or_schedule",
                return_value=({}, [], {"qmd_derived_status": "ready"}),
            ) as qmd,
            patch(
                "src.backend.watchlist_runtime_service.project_watchlists_from_candidates",
                return_value={"status": "ready", "watchlists": [{"watchlist_id": "core-candidates", "members": []}]},
            ),
        ):
            payload = scanner_snapshot_payload(as_of=as_of)

        row = payload["rows"][0]
        self.assertEqual(row["company_name"], "APPLE INC")
        self.assertEqual(row["float_shares"], 14_400_000_000)
        self.assertEqual(row["market_cap_category"], "Large Cap")
        self.assertEqual(row["float_category"], "Broad Float")
        self.assertEqual(row["float_quality"], "reported")
        self.assertEqual(row["short_interest_pct"], 1.0017)
        self.assertEqual(row["short_volume_pct"], 41.2)
        self.assertEqual(row["borrow_status"], "shortable")
        self.assertEqual(row["logo_url"], "/api/real-live-trading/logo?path=branding%2Flogo%2Faapl.svg")
        self.assertEqual(row["xbrl_quality_score"], 78.0)
        self.assertEqual(row["fundamental_operating_margin_pct"], 32.0)
        self.assertEqual(row["live_news_recency"], "none")
        self.assertEqual(row["sec_recency"], "none")
        self.assertEqual(payload["meta"]["field_coverage"]["company_name"], 100.0)
        self.assertEqual(payload["meta"]["field_coverage"]["exchange"], 0.0)
        self.assertEqual(payload["meta"]["field_coverage"]["xbrl_quality_score"], 100.0)
        self.assertEqual(payload["meta"]["field_coverage"]["float_quality"], 100.0)
        self.assertEqual(payload["meta"]["field_coverage"]["short_volume"], 100.0)
        self.assertEqual(payload["errors"], {})
        self.assertEqual(payload["as_of"], "2026-07-17T13:44:00+00:00")
        self.assertEqual(payload["watchlist_runtime"]["status"], "ready")
        self.assertEqual(
            qmd.call_args.args[0], datetime(2026, 7, 17, 13, 44, tzinfo=UTC)
        )
        self.assertFalse(qmd.call_args.kwargs["schedule_missing"])

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
        sec = [{
            "accepted_at_utc": "2026-07-17T11:00:00Z",
            "form_type": "8-K",
            "sec_review": {"status": "complete", "result": {"fundamental_direction": "positive"}},
            "sec_synthesis": {"synthesis": {"composite_sentiment": "mixed"}},
            "ticker": "AAPL",
        }]

        _enrich_scanner_intelligence(rows, news, sec, as_of)

        self.assertEqual(rows[0]["live_news_count"], 1)
        self.assertEqual(rows[0]["live_news_recency"], "hot")
        self.assertEqual(rows[0]["news_labels"], "earnings, guidance")
        self.assertEqual(rows[0]["sec_recency"], "hot")
        self.assertEqual(rows[0]["sec_labels"], "8-K")
        self.assertEqual(rows[0]["sec_synthesis_direction"], "mixed")
        self.assertEqual(rows[0]["sec_review_status"], "complete")
        self.assertEqual(rows[0]["sec_review_fundamental_direction"], "positive")

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
            count_sql = clickhouse.call_args_list[1].args[0]
            _query_scanner_sec_intelligence(as_of)
            sec_sql = clickhouse.call_args.args[0]

        self.assertIn("GROUP BY ticker", count_sql)
        self.assertIn("uniqExactIf", count_sql)
        self.assertIn("America/New_York", count_sql)
        self.assertIn("benzinga_news_event_v2 FINAL", count_sql)
        self.assertNotIn("LIMIT 30", count_sql)
        self.assertIn("GROUP BY ticker", sec_sql)
        self.assertIn("valid_to_date_exclusive", sec_sql)
        self.assertIn("sec_synthesis_v1 AS s FINAL", sec_sql)
        self.assertIn("sec_llm_issuer_review_v1 AS r FINAL", sec_sql)
        self.assertIn("s.updated_at_utc <=", sec_sql)
        self.assertIn("r.updated_at_utc <=", sec_sql)
        self.assertIn("sec_review_fundamental_direction", sec_sql)
        self.assertNotIn("LIMIT 30", sec_sql)

    def test_aggregated_scanner_intelligence_populates_labels_and_recency(self) -> None:
        as_of = datetime(2026, 7, 17, 13, 45, tzinfo=UTC)
        rows = [{"symbol": "AAPL"}, {"symbol": "MSFT"}]
        news = [{"ticker": "AAPL", "live_news_count": 2, "latest_news_at": "2026-07-17T13:15:00Z", "news_labels": ["guidance", "earnings"]}]
        sec = [{
            "ticker": "AAPL", "sec_count": 1, "latest_sec_at": "2026-07-16T20:00:00Z", "sec_labels": ["8-K"],
            "sec_synthesis_count": 1, "sec_synthesis_direction": "negative", "sec_review_status": "complete",
            "sec_review_fundamental_direction": "contextual",
        }]

        _merge_scanner_intelligence(rows, news, sec, as_of)

        self.assertEqual(rows[0]["news_labels"], "earnings, guidance")
        self.assertEqual(rows[0]["live_news_recency"], "hot")
        self.assertEqual(rows[0]["sec_labels"], "8-K")
        self.assertEqual(rows[0]["sec_recency"], "cold")
        self.assertEqual(rows[0]["sec_synthesis_direction"], "negative")
        self.assertEqual(rows[0]["sec_review_fundamental_direction"], "contextual")
        self.assertEqual(rows[1]["live_news_recency"], "none")


if __name__ == "__main__":
    unittest.main()
