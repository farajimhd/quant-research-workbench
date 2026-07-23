from __future__ import annotations

import unittest
from datetime import UTC, datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from src.backend.historical_scanner_service import (
    _materialize_technical_snapshot,
    historical_scanner_technical_projection,
    scanner_technical_window,
)


NEW_YORK = ZoneInfo("America/New_York")


class ScannerTechnicalWindowTests(unittest.TestCase):
    def test_five_minute_boundary_uses_the_completed_bucket(self) -> None:
        start, end = scanner_technical_window(
            datetime(2026, 7, 17, 9, 45, tzinfo=NEW_YORK),
            "5m",
        )
        self.assertEqual(start.astimezone(NEW_YORK).strftime("%H:%M:%S"), "09:40:00")
        self.assertEqual(end.astimezone(NEW_YORK).strftime("%H:%M:%S"), "09:45:00")

    def test_hour_bucket_is_aligned_to_extended_session_grid(self) -> None:
        start, end = scanner_technical_window(
            datetime(2026, 7, 17, 9, 45, tzinfo=NEW_YORK),
            "1h",
        )
        self.assertEqual(start.astimezone(NEW_YORK).strftime("%H:%M:%S"), "09:00:00")
        self.assertEqual(end.astimezone(NEW_YORK).strftime("%H:%M:%S"), "09:45:00")

    def test_daily_bucket_starts_at_extended_session_open(self) -> None:
        start, end = scanner_technical_window(
            datetime(2026, 7, 17, 9, 45, tzinfo=NEW_YORK),
            "1d",
        )
        self.assertEqual(start.astimezone(NEW_YORK).strftime("%H:%M:%S"), "04:00:00")
        self.assertEqual(end.astimezone(NEW_YORK).strftime("%H:%M:%S"), "09:45:00")

    def test_extended_session_anchor_is_not_an_interval_bucket(self) -> None:
        start, end = scanner_technical_window(
            datetime(2026, 7, 17, 9, 47, 31, tzinfo=NEW_YORK),
            "extended_session",
        )
        self.assertEqual(start.astimezone(NEW_YORK).strftime("%H:%M:%S"), "04:00:00")
        self.assertEqual(end.astimezone(NEW_YORK).strftime("%H:%M:%S"), "09:47:31")

    def test_regular_session_anchor_starts_at_market_open(self) -> None:
        start, end = scanner_technical_window(
            datetime(2026, 7, 17, 9, 47, 31, tzinfo=NEW_YORK),
            "regular_session",
        )
        self.assertEqual(start.astimezone(NEW_YORK).strftime("%H:%M:%S"), "09:30:00")
        self.assertEqual(end.astimezone(NEW_YORK).strftime("%H:%M:%S"), "09:47:31")

    def test_before_session_uses_the_previous_completed_weekday(self) -> None:
        start, end = scanner_technical_window(
            datetime(2026, 7, 20, 3, 0, tzinfo=NEW_YORK),
            "1d",
        )
        self.assertEqual(start.astimezone(NEW_YORK).isoformat(), "2026-07-17T04:00:00-04:00")
        self.assertEqual(end.astimezone(NEW_YORK).isoformat(), "2026-07-17T20:00:00-04:00")

    def test_vwap_supports_standard_hlc3_and_exact_trade_sources(self) -> None:
        class CaptureClient:
            sql = ""

            def execute(self, sql: str, **_kwargs) -> str:
                self.sql = sql
                return ""

        client = CaptureClient()
        _materialize_technical_snapshot(
            client,
            source_database="market_sip_compact",
            table_prefix="events_",
            snapshot_at=datetime(2026, 7, 17, 13, 45, tzinfo=UTC),
            window_start=datetime(2026, 7, 17, 8, 0, tzinfo=UTC),
            calculation_window="extended_session",
            source_revision="test",
        )
        self.assertIn("sumIf(price * toFloat64(size_primary), is_trade) AS bar_dollar_volume", client.sql)
        self.assertIn("sum(((bar_high + bar_low + bar_close) / 3) * bar_volume) / volume) AS vwap", client.sql)
        self.assertIn("if(volume = 0, 0, dollar_volume / volume) AS vwap_trade", client.sql)
        self.assertIn("'extended_session'", client.sql)
        self.assertIn("calculation_window", client.sql)

    def test_projection_exposes_source_specific_vwap_keys(self) -> None:
        class ProjectionClient:
            def __init__(self, *_args) -> None:
                pass

            def execute(self, sql: str, **_kwargs) -> str:
                if "events_ordinal_continuity" in sql:
                    return '{"event_count":"1200","build_step":"7","updated_at":"2026-07-17 14:00:00"}\n'
                if "SELECT" in sql and "symbol, open, high" in sql:
                    return '{"symbol":"AAPL","vwap":201.5,"vwap_distance_pct":0.25,"vwap_trade":201.45,"vwap_trade_distance_pct":0.27,"relative_volume":1.4}\n'
                return ""

        with patch("src.backend.historical_scanner_service.ClickHouseHttpClient", ProjectionClient):
            projection, meta = historical_scanner_technical_projection(
                datetime(2026, 7, 17, 13, 45, tzinfo=UTC),
                calculation_windows=["extended_session"],
            )

        self.assertEqual(projection["AAPL"]["technical__vwap__extended_session__hlc3"], 201.5)
        self.assertEqual(projection["AAPL"]["technical__vwap__extended_session__trade_price"], 201.45)
        self.assertEqual(projection["AAPL"]["technical__relative_volume__extended_session"], 1.4)
        self.assertEqual(meta["technical_calculation_windows"], ["extended_session"])


if __name__ == "__main__":
    unittest.main()
