import unittest
import urllib.error
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
            return_value=[{"ticker": "AAPL", "issuer_name": "Apple Inc.", "logo_relative_path": ""}],
        ):
            payload = ticker_presentation_payload(["AAPL"])

        self.assertEqual(payload["status"], "ready")
        self.assertEqual(payload["presentations"]["AAPL"]["logo_url"], "")


if __name__ == "__main__":
    unittest.main()
