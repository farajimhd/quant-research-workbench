from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import patch

from src.backend.canvas_preview_service import _attach_sec_tickers, canvas_preview_payload


class CanvasPreviewServiceTests(unittest.TestCase):
    @patch("src.backend.canvas_preview_service.historical_day_coverage", return_value={"event_count": 1000, "ticker_count": 100})
    @patch("src.backend.canvas_preview_service._clickhouse_rows", return_value=[{"title": "context"}])
    @patch("src.backend.canvas_preview_service.historical_bar_chunk")
    def test_preview_is_anchored_at_selected_clock(self, bars_mock, _clickhouse_mock, _coverage_mock) -> None:
        bars_mock.return_value = {
            "bars": [
                {"bar_start": "2026-07-10T13:44:00Z", "open": 100.0, "close": 101.0, "volume": 50, "trade_count": 4, "quote_count": 8}
            ]
        }

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
        self.assertEqual(len(payload["scanner"]), 6)
        self.assertAlmostEqual(payload["scanner"][0]["change_5m_pct"], 1.0)
        self.assertEqual(payload["scanner"][0]["live_news_recency"], "none")
        self.assertTrue(payload["portfolio"]["fixture"])
        self.assertEqual(payload["orders"][0]["acctId"], "DU0000000")
        scanner_call = next(call for call in bars_mock.call_args_list if call.kwargs["ticker"] == "AAPL")
        self.assertEqual(scanner_call.kwargs["window_minutes"], 15)
        self.assertEqual(scanner_call.kwargs["offset_minutes"], 330)

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


if __name__ == "__main__":
    unittest.main()
