from datetime import UTC, datetime
import unittest
from unittest.mock import patch

from src.backend.canvas_preview_service import scanner_snapshot_payload


class CanvasScannerPayloadTest(unittest.TestCase):
    def test_reference_fields_merge_and_publish_coverage(self) -> None:
        as_of = datetime(2026, 7, 17, 13, 45, tzinfo=UTC)
        snapshot = ([{"symbol": "AAPL", "last": 200.0, "change_pct": 1.0, "change_5m_pct": 0.5}], {"row_count": 1})
        projection = {
            "AAPL": {
                "company_name": "APPLE INC",
                "country": "US",
                "market_cap": 4_374_000_000_000,
                "shares_outstanding": 14_687_000_000,
                "float_shares": 14_400_000_000,
                "short_interest": 144_248_000,
                "short_crowding_pct": 1.0017,
                "days_to_cover": 2.76,
            }
        }
        with (
            patch("src.backend.canvas_preview_service.historical_scanner_snapshot", return_value=snapshot),
            patch("src.backend.canvas_preview_service.historical_scanner_reference_projection", return_value=projection),
            patch("src.backend.canvas_preview_service._query_news", return_value=[]),
            patch("src.backend.canvas_preview_service._query_sec", return_value=[]),
            patch("src.backend.canvas_preview_service._attach_sec_tickers"),
        ):
            payload = scanner_snapshot_payload(as_of=as_of)

        row = payload["rows"][0]
        self.assertEqual(row["company_name"], "APPLE INC")
        self.assertEqual(row["float_shares"], 14_400_000_000)
        self.assertEqual(row["live_news_recency"], "none")
        self.assertEqual(row["sec_recency"], "none")
        self.assertEqual(payload["meta"]["field_coverage"]["company_name"], 100.0)
        self.assertEqual(payload["meta"]["field_coverage"]["exchange"], 0.0)
        self.assertEqual(payload["errors"], {})


if __name__ == "__main__":
    unittest.main()
