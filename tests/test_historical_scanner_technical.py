from __future__ import annotations

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from src.backend.historical_scanner_service import scanner_technical_window


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

    def test_before_session_uses_the_previous_completed_weekday(self) -> None:
        start, end = scanner_technical_window(
            datetime(2026, 7, 20, 3, 0, tzinfo=NEW_YORK),
            "1d",
        )
        self.assertEqual(start.astimezone(NEW_YORK).isoformat(), "2026-07-17T04:00:00-04:00")
        self.assertEqual(end.astimezone(NEW_YORK).isoformat(), "2026-07-17T20:00:00-04:00")


if __name__ == "__main__":
    unittest.main()
