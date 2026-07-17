from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import patch

from src.backend.trading_runtime_service import (
    historical_bar_chunk,
    historical_compact_events,
    historical_latest_coverage,
    historical_preflight,
    historical_window_preview,
    market_event_references,
)


class HistoricalTradingServiceTests(unittest.TestCase):
    def test_market_event_references_expose_displayable_venues_and_conditions(self) -> None:
        market_event_references.cache_clear()
        payload = market_event_references()

        self.assertEqual(payload["exchanges"]["11"]["name"], "NYSE Arca, Inc.")
        self.assertEqual(payload["exchanges"]["11"]["mic"], "ARCX")
        self.assertEqual(payload["conditions"]["60"]["name"], "Regular Sale")
        self.assertEqual(payload["conditions"]["60"]["sip_mapping"], "@")
        self.assertEqual(payload["conditions"]["96"]["name"], "Odd Lot Trade")

    @patch("src.backend.trading_runtime_service._historical_gateway_get")
    def test_compact_events_request_latest_rows_before_canvas_clock(self, gateway_get) -> None:
        gateway_get.return_value = [{"ticker": "AAPL", "arrival_sequence": 9}, "invalid"]

        payload = historical_compact_events(
            "aapl",
            start="2026-07-14T04:00:00-04:00",
            end="2026-07-14T09:45:00-04:00",
            row_limit=500,
        )

        self.assertEqual(payload, [{"ticker": "AAPL", "arrival_sequence": 9}])
        gateway_get.assert_called_once_with(
            "/snapshot/compact-events/AAPL",
            {
                "start": "2026-07-14T04:00:00-04:00",
                "end": "2026-07-14T09:45:00-04:00",
                "limit": 500,
                "tail": "true",
            },
            timeout=15,
        )

    @patch("src.backend.trading_runtime_service._historical_gateway_get", return_value={"session_date": "2026-07-10", "event_count": 10})
    def test_latest_coverage_comes_from_history_gateway(self, gateway_get) -> None:
        payload = historical_latest_coverage()
        self.assertEqual(payload["session_date"], "2026-07-10")
        gateway_get.assert_called_once_with("/coverage/latest", {}, timeout=15)

    def test_replay_window_is_always_exactly_one_day(self) -> None:
        payload = historical_window_preview(
            mode="replay",
            anchor_date=date(2026, 7, 10),
            session_count=1,
            replay_end_date=date(2026, 7, 13),
        )

        self.assertEqual(payload["sessions"], ["2026-07-10"])
        self.assertEqual(payload["session_count"], 1)
        self.assertEqual(payload["start"], "2026-07-10T04:00:00-04:00")
        self.assertEqual(payload["end"], "2026-07-10T20:00:00-04:00")

    @patch("src.backend.trading_runtime_service.list_strategy_definitions", return_value=[])
    @patch(
        "src.backend.trading_runtime_service.historical_gateway_snapshot",
        return_value={"ready": True, "health": {"source": "market_sip_compact.events_YYYY"}},
    )
    @patch("src.backend.trading_runtime_service._historical_gateway_get")
    def test_replay_preflight_uses_real_event_coverage(
        self,
        gateway_get,
        _gateway_snapshot,
        _strategies,
    ) -> None:
        gateway_get.side_effect = [
            {
                "event_count": 1_250_000,
                "ticker_count": 4_200,
                "first_sip_timestamp_us": 1_783_655_200_000_000,
                "last_sip_timestamp_us": 1_783_712_799_000_000,
                "source_tables": ["market_sip_compact.events_2026"],
            },
        ]

        payload = historical_preflight(
            mode="replay",
            anchor_date=date(2026, 7, 10),
            session_count=1,
        )

        self.assertTrue(payload["market_ready"])
        self.assertEqual(payload["coverage"]["event_count"], 1_250_000)
        self.assertEqual(payload["coverage"]["ticker_count"], 4_200)
        checks = {row["id"]: row for row in payload["checks"]}
        self.assertEqual(checks["market_data"]["status"], "ready")
        self.assertFalse(checks["strategy_authority"]["required"])
        self.assertFalse(checks["run_controller"]["required"])

    @patch("src.backend.trading_runtime_service.list_strategy_definitions", return_value=[])
    @patch(
        "src.backend.trading_runtime_service.historical_gateway_snapshot",
        return_value={"ready": True, "health": {"source": "market_sip_compact.events_YYYY"}},
    )
    @patch("src.backend.trading_runtime_service._historical_gateway_get", return_value=[])
    def test_backtest_preflight_reports_strategy_and_controller_as_required_blockers(
        self,
        _gateway_get,
        _gateway_snapshot,
        _strategies,
    ) -> None:
        payload = historical_preflight(
            mode="backtest",
            anchor_date=date(2026, 7, 13),
            session_count=5,
        )

        self.assertFalse(payload["strategy_run_ready"])
        checks = {row["id"]: row for row in payload["checks"]}
        self.assertEqual(checks["strategy_authority"]["status"], "blocked")
        self.assertTrue(checks["strategy_authority"]["required"])
        self.assertEqual(checks["run_controller"]["status"], "blocked")
        self.assertTrue(checks["run_controller"]["required"])

    def test_replay_chunks_are_bounded_to_one_day(self) -> None:
        with self.assertRaisesRegex(ValueError, "offset_minutes"):
            historical_bar_chunk(
                anchor_date=date(2026, 7, 10),
                ticker="AAPL",
                timeframe="1m",
                offset_minutes=960,
            )


if __name__ == "__main__":
    unittest.main()
