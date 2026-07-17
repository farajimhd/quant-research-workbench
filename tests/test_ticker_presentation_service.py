from __future__ import annotations

import unittest
from unittest.mock import patch

from src.backend.ticker_presentation_service import normalize_tickers, ticker_presentation_payload, ticker_presentation_sql


class TickerPresentationServiceTests(unittest.TestCase):
    def test_normalize_tickers_deduplicates_and_rejects_invalid_symbols(self) -> None:
        self.assertEqual(normalize_tickers([" aapl ", "MSFT", "AAPL", "bad ticker", ""]), ["AAPL", "MSFT"])

    def test_query_uses_presentation_asset_authority(self) -> None:
        sql = ticker_presentation_sql(["AAPL", "MSFT"])
        self.assertIn("market_presentation_asset_v1 FINAL", sql)
        self.assertIn("feature_tradable_universe_v1 AS u FINAL", sql)
        self.assertIn("u.ticker IN ('AAPL', 'MSFT')", sql)
        self.assertNotIn("max(universe_date) FROM `q_live`.feature_tradable_universe_v1 FINAL", sql)
        self.assertIn("coalesce(scanner.logo_asset_id, issuer.logo_asset_id)", sql)
        self.assertIn("LIMIT 1 BY ticker", sql)

    @patch("src.backend.ticker_presentation_service._clickhouse_rows")
    def test_payload_converts_storage_path_to_existing_logo_endpoint(self, rows_mock) -> None:
        rows_mock.return_value = [{"ticker": "AAPL", "issuer_name": "Apple", "logo_relative_path": "logos/aapl.svg"}]
        payload = ticker_presentation_payload(["AAPL"])
        self.assertEqual(payload["presentations"]["AAPL"]["logo_url"], "/api/real-live-trading/logo?path=logos%2Faapl.svg")
        self.assertEqual(payload["presentations"]["AAPL"]["issuer_name"], "Apple")
        self.assertEqual(payload["status"], "ready")

    @patch("src.backend.ticker_presentation_service._clickhouse_rows")
    def test_payload_degrades_without_failing_when_optional_branding_is_unavailable(self, rows_mock) -> None:
        rows_mock.side_effect = RuntimeError("ClickHouse memory limit exceeded")
        payload = ticker_presentation_payload(["AAPL"])
        self.assertEqual(payload["presentations"], {})
        self.assertEqual(payload["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
