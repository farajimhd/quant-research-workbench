import unittest
from unittest.mock import patch

from src.backend.app import (
    _qmd_stream_payload_matches_ticker,
    trading_canvas_market_events,
)


class QmdCanvasStreamContractTests(unittest.TestCase):
    def test_stream_control_frames_bypass_ticker_filter(self) -> None:
        self.assertTrue(
            _qmd_stream_payload_matches_ticker(
                {
                    "type": "stream_gap",
                    "action": "resnapshot_required",
                    "skipped": 3,
                },
                "AAPL",
            )
        )
        self.assertTrue(
            _qmd_stream_payload_matches_ticker(
                {"error": "upstream unavailable"},
                "AAPL",
            )
        )

    def test_stream_data_frames_remain_ticker_scoped(self) -> None:
        self.assertTrue(_qmd_stream_payload_matches_ticker({"ticker": "aapl"}, "AAPL"))
        self.assertFalse(_qmd_stream_payload_matches_ticker({"ticker": "MSFT"}, "AAPL"))
        self.assertFalse(_qmd_stream_payload_matches_ticker({"status": "connected"}, "AAPL"))

    def test_market_event_snapshot_exposes_sequence_and_completeness(self) -> None:
        with (
            patch(
                "src.backend.app.qmd_compact_event_page",
                return_value={
                    "buffer_end_arrival_sequence": 19,
                    "buffer_start_arrival_sequence": 11,
                    "cursor_expired": False,
                    "events": [{"arrival_sequence": 19, "ticker": "AAPL"}],
                    "has_more": False,
                    "next_after_arrival_sequence": 19,
                    "truncated_before": True,
                },
            ),
            patch("src.backend.app.market_event_references", return_value={}),
        ):
            payload = trading_canvas_market_events("aapl", row_limit=250)

        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["snapshot_id"], "qmd-compact:AAPL:19")
        self.assertEqual(payload["last_sequence"], 19)
        self.assertIs(payload["complete"], True)
        self.assertIs(payload["truncated_before"], True)


if __name__ == "__main__":
    unittest.main()
