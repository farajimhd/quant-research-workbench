from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import patch

from src.backend.canvas_preview_service import canvas_preview_payload


class CanvasPreviewServiceTests(unittest.TestCase):
    @patch("src.backend.canvas_preview_service._clickhouse_rows", return_value=[{"title": "context"}])
    @patch("src.backend.canvas_preview_service.historical_bar_chunk")
    def test_preview_is_anchored_at_selected_clock(self, bars_mock, _clickhouse_mock) -> None:
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
        self.assertEqual(payload["chart"]["bars"][-1]["bar_start"], "2026-07-10T13:44:00Z")
        self.assertEqual(len(payload["scanner"]), 6)
        self.assertTrue(payload["portfolio"]["fixture"])
        self.assertEqual(payload["orders"][0]["acctId"], "DU0000000")
        chart_call = next(call for call in bars_mock.call_args_list if call.kwargs["ticker"] == "AAPL" and call.kwargs["window_minutes"] == 30)
        self.assertEqual(chart_call.kwargs["offset_minutes"], 315)

    def test_preview_rejects_invalid_clock(self) -> None:
        with self.assertRaisesRegex(ValueError, "preview_time"):
            canvas_preview_payload(
                session_date=date(2026, 7, 10),
                preview_time="9:45",
                chart_symbol="AAPL",
                chart_timeframe="1m",
            )


if __name__ == "__main__":
    unittest.main()
