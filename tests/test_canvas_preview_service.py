from __future__ import annotations

import unittest
from datetime import date, datetime
from unittest.mock import patch

from src.backend.canvas_preview_service import (
    _attach_sec_tickers,
    _merge_scanner_intelligence,
    canvas_preview_payload,
)


class CanvasPreviewServiceTests(unittest.TestCase):
    @patch("src.backend.canvas_preview_service.historical_day_coverage", return_value={"event_count": 1000, "ticker_count": 100})
    @patch("src.backend.canvas_preview_service._clickhouse_rows", return_value=[{"title": "context"}])
    @patch(
        "src.backend.canvas_preview_service.historical_scanner_snapshot",
        return_value=(
            [{"symbol": "AAPL", "last": 101.0, "change_5m_pct": 1.0}],
            {"complete_universe": True, "row_count": 1, "status": "ready"},
        ),
    )
    def test_preview_is_anchored_at_selected_clock(self, scanner_mock, _clickhouse_mock, _coverage_mock) -> None:

        with patch(
            "src.backend.canvas_preview_service.strategy_canvas_payload",
            return_value={"signals": []},
        ):
            payload = canvas_preview_payload(
                session_date=date(2026, 7, 10),
                preview_time="09:45",
                chart_symbol="aapl",
                chart_timeframe="1m",
            )

        self.assertEqual(payload["as_of"], "2026-07-10T09:45:00-04:00")
        self.assertEqual(payload["chart"]["symbol"], "AAPL")
        self.assertEqual(payload["chart"]["bars"], [])
        self.assertEqual(payload["coverage"]["event_count"], 1000)
        self.assertEqual(len(payload["scanner"]), 1)
        self.assertAlmostEqual(payload["scanner"][0]["change_5m_pct"], 1.0)
        self.assertEqual(payload["scanner"][0]["live_news_recency"], "none")
        self.assertTrue(payload["portfolio"]["fixture"])
        self.assertEqual(payload["orders"][0]["acctId"], "DU0000000")
        preview_account = payload["trading"]["portfolio"]["management"]["accounts"][0]
        self.assertEqual(preview_account["managed_order_groups"], [])
        self.assertEqual(preview_account["pending_operational_commands"], [])
        self.assertEqual(preview_account["continuous_risk"], {})
        scanner_mock.assert_called_once()
        self.assertEqual(scanner_mock.call_args.kwargs["lookback_minutes"], 15)

    def test_preview_rejects_invalid_clock(self) -> None:
        with self.assertRaisesRegex(ValueError, "preview_time"):
            canvas_preview_payload(
                session_date=date(2026, 7, 10),
                preview_time="9:45",
                chart_symbol="AAPL",
                chart_timeframe="1m",
            )

    @patch("src.backend.canvas_preview_service._clickhouse_rows", return_value=[{"cik": "0000320193", "mapped_ticker": "AAPL"}])
    def test_sec_identity_batch_query_does_not_reuse_ticker_alias_in_where(self, clickhouse_mock) -> None:
        rows = [{"cik": "0000320193", "form_type": "10-Q"}]

        _attach_sec_tickers(rows)

        self.assertEqual(rows[0]["ticker"], "AAPL")
        query = clickhouse_mock.call_args.args[0]
        self.assertIn("AS mapped_ticker", query)
        self.assertIn("notEmpty(ticker)", query)

    def test_scanner_news_projection_exposes_combined_news_contract(self) -> None:
        scanner = [{"symbol": "XPON"}]
        news = [{
            "ticker": "XPON",
            "canonical_news_id": "news-1",
            "title": "Issuer acquisition",
            "latest_news_at": "2026-08-24T15:00:00Z",
            "live_news_count": 1,
            "today_news_count": 1,
            "communication_purpose": "report",
            "information_origin": "issuer",
            "document_structure": "single_subject",
            "synthesis_direction": "neutral",
            "news_labels": ["corporate_transaction.acquisition"],
            "text_availability": "title_only",
            "ai_state": {
                "funnel": {"eligible_probability": 1.0, "forecast_eligibility": "eligible"},
                "review": {"status": "complete", "labels": {"issuers": [{
                    "ticker": "XPON",
                    "forecast_relevance_probability": 0.92,
                    "positive_implication_probability": 0.71,
                    "negative_implication_probability": 0.18,
                }]}},
                "hypotheses": [{"ticker": "XPON", "prediction": {
                    "regime_compatibility": "supportive",
                    "predictions": {"5m": {
                        "expected_return_pct": 1.25,
                        "confidence": 0.66,
                        "upside_probability": 0.7,
                        "downside_probability": 0.2,
                    }},
                }}],
            },
        }]

        _merge_scanner_intelligence(
            scanner,
            news,
            [],
            datetime.fromisoformat("2026-08-24T16:00:00+00:00"),
        )

        self.assertEqual(scanner[0]["news_synthesis_class"], "Company")
        self.assertEqual(scanner[0]["today_news_count"], 1)
        self.assertEqual(scanner[0]["latest_news_published_at"], "2026-08-24T15:00:00Z")
        self.assertEqual(scanner[0]["news_ai_review"], 92.0)
        self.assertEqual(scanner[0]["news_deepfm_probability"], 100.0)
        self.assertEqual(scanner[0]["news_ai_reaction"], 1.25)


if __name__ == "__main__":
    unittest.main()
