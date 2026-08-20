import unittest
import urllib.error
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from src.backend.ticker_presentation_service import ticker_presentation_payload


class TickerPresentationServiceTest(unittest.TestCase):
    def test_database_transport_failure_is_reported_as_retryable_unavailable(self) -> None:
        with patch(
            "src.backend.ticker_presentation_service._clickhouse_rows",
            side_effect=urllib.error.URLError("database offline"),
        ):
            payload = ticker_presentation_payload(["AAPL"])

        self.assertEqual(payload["status"], "unavailable")
        self.assertEqual(payload["presentations"], {})

    def test_missing_logo_path_stays_empty_without_fallback_asset(self) -> None:
        with patch(
            "src.backend.ticker_presentation_service._clickhouse_rows",
            return_value=[{"ticker": "AAPL", "issuer_name": "Apple Inc.", "country": "US", "logo_relative_path": ""}],
        ):
            payload = ticker_presentation_payload(["AAPL"])

        self.assertEqual(payload["status"], "ready")
        self.assertEqual(payload["presentations"]["AAPL"]["logo_url"], "")
        self.assertEqual(payload["presentations"]["AAPL"]["country"], "US")

    def test_live_recency_is_bounded_to_requested_tickers_and_typed(self) -> None:
        now = datetime.now(UTC)

        def rows(query: str) -> list[dict[str, object]]:
            if "live_news_count" in query:
                self.assertIn("ticker IN ('AAPL')", query)
                return [{"ticker": "AAPL", "latest_news_at": (now - timedelta(hours=1)).isoformat()}]
            if "sec_count" in query:
                self.assertIn("upperUTF8(trimBoth(b.ticker)) IN ('AAPL')", query)
                return [{"ticker": "AAPL", "latest_sec_at": (now - timedelta(hours=12)).isoformat()}]
            return [{"ticker": "AAPL", "issuer_name": "Apple Inc.", "country": "US", "logo_relative_path": ""}]

        with patch("src.backend.ticker_presentation_service._clickhouse_rows", side_effect=rows):
            payload = ticker_presentation_payload(["AAPL"], include_recency=True)

        self.assertEqual(payload["status"], "ready")
        self.assertEqual(payload["presentations"]["AAPL"]["live_news_recency"], "hot")
        self.assertEqual(payload["presentations"]["AAPL"]["sec_recency"], "cold")

    def test_optional_recency_failure_does_not_hide_ticker_identity(self) -> None:
        def rows(query: str) -> list[dict[str, object]]:
            if "market_presentation_asset_v1" in query:
                return [{"ticker": "AAPL", "issuer_name": "Apple Inc.", "country": "US", "logo_relative_path": ""}]
            raise TimeoutError("optional authority unavailable")

        with patch("src.backend.ticker_presentation_service._clickhouse_rows", side_effect=rows):
            payload = ticker_presentation_payload(["AAPL"], include_recency=True)

        self.assertEqual(payload["status"], "ready")
        self.assertEqual(payload["presentations"]["AAPL"]["issuer_name"], "Apple Inc.")
        self.assertEqual(payload["presentations"]["AAPL"]["live_news_recency"], "none")


if __name__ == "__main__":
    unittest.main()
