from __future__ import annotations

import unittest
import urllib.error
from datetime import date
from unittest.mock import patch

from src.backend.trading_runtime_service import (
    _historical_gateway_get,
    historical_bar_chunk,
    historical_compact_events,
    historical_latest_coverage,
    historical_market_state,
    historical_microstructure_forecast,
    historical_ticker_change,
    historical_preflight,
    historical_window_preview,
    market_event_references,
)


class HistoricalTradingServiceTests(unittest.TestCase):
    @patch("src.backend.trading_runtime_service.urllib.request.urlopen")
    def test_history_gateway_connection_failure_is_actionable(self, urlopen) -> None:
        urlopen.side_effect = urllib.error.URLError(ConnectionRefusedError(10061, "refused"))

        with self.assertRaisesRegex(
            RuntimeError,
            r"QMD History gateway is not reachable at .*run_qmd_history_gateway\.ps1.*health status",
        ):
            _historical_gateway_get("/health", {}, timeout=3)

    @patch("src.backend.trading_runtime_service.historical_compact_events")
    @patch("src.backend.trading_runtime_service.historical_macro_bar_history")
    def test_ticker_change_compares_current_trade_with_prior_20_et_close(self, macro_history, compact_events) -> None:
        macro_history.return_value = {"history": [
            {"session_date": "2026-07-13", "close": 317.88},
            {"session_date": "2026-07-14", "close": 320.00},
        ]}
        compact_events.return_value = [{"event_meta": 1, "price_primary_int": 31481}]

        payload = historical_ticker_change("aapl", as_of="2026-07-14T09:45:00-04:00")

        self.assertEqual(payload["previous_session_date"], "2026-07-13")
        self.assertEqual(payload["current_price"], 314.81)
        self.assertAlmostEqual(payload["percent_change"], -0.9657732478)
        compact_events.assert_called_once_with(
            "AAPL",
            start="2026-07-14T04:00:00-04:00",
            end="2026-07-14T09:45:00-04:00",
            row_limit=5_000,
        )

    @patch("src.backend.trading_runtime_service._historical_gateway_get")
    def test_historical_market_state_uses_condition_transitions_and_qmd_luld_bar(self, gateway_get) -> None:
        gateway_get.side_effect = [
            {"rows": [
                {"last_event_timestamp_us": 10, "condition_halt_pause_flag": 1, "bar_end": "2026-07-14T13:40:00Z"},
                {"last_event_timestamp_us": 20, "condition_resume_flag": 1, "bar_end": "2026-07-14T13:41:00Z"},
            ]},
            {"bars": [{
                "estimated_luld_active": True,
                "estimated_luld_state": "near_upper",
                "estimated_luld_lower_price": 280.0,
                "estimated_luld_upper_price": 320.0,
                "estimated_luld_distance_to_lower_pct": 9.0,
                "estimated_luld_distance_to_upper_pct": 0.8,
            }]},
        ]

        payload = historical_market_state(
            "aapl",
            start="2026-07-14T04:00:00-04:00",
            end="2026-07-14T09:45:00-04:00",
        )

        self.assertEqual(payload["trading_status"], "resumed")
        self.assertTrue(payload["is_tradable"])
        self.assertEqual(payload["luld_state"], "near_upper")
        self.assertEqual(payload["luld_upper_price"], 320.0)

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

    @patch("src.backend.trading_runtime_service._historical_gateway_get")
    def test_microstructure_forecast_uses_shared_history_gateway_contract(self, gateway_get) -> None:
        gateway_get.return_value = {
            "schema_version": 1,
            "method": "deterministic_microstructure_v2",
            "ticker": "AAPL",
            "horizons": [{"horizon_events": 25, "direction": "up"}],
        }

        payload = historical_microstructure_forecast(
            "aapl",
            start="2026-07-14T04:00:00-04:00",
            end="2026-07-14T09:45:00-04:00",
        )

        self.assertEqual(payload["method"], "deterministic_microstructure_v2")
        gateway_get.assert_called_once_with(
            "/snapshot/microstructure-forecast/AAPL",
            {
                "start": "2026-07-14T04:00:00-04:00",
                "end": "2026-07-14T09:45:00-04:00",
                "limit": 1_024,
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
