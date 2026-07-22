from datetime import UTC, datetime
import unittest
from unittest.mock import patch

from src.backend.historical_scanner_service import historical_scanner_reference_projection, historical_scanner_snapshot


class FakeClient:
    calls: list[str] = []

    def __init__(self, *_args) -> None:
        self.read_count = 0

    def execute(self, sql: str, **_kwargs) -> str:
        FakeClient.calls.append(sql)
        if "events_ordinal_continuity" in sql:
            return '{"event_count":"1200","build_step":"7","updated_at":"2026-07-17 14:00:00"}\n'
        if "SELECT symbol" in sql:
            self.read_count += 1
            return "" if self.read_count == 1 else '{"symbol":"AAPL","last":200,"change_pct":1.5,"change_5m_pct":0.4,"volume":1000,"trade_count":10,"quote_count":20}\n'
        return ""


class HistoricalScannerServiceTest(unittest.TestCase):
    def test_full_universe_snapshot_is_materialized_once_and_revision_keyed(self) -> None:
        FakeClient.calls = []
        with patch("src.backend.historical_scanner_service.ClickHouseHttpClient", FakeClient):
            rows, meta = historical_scanner_snapshot(datetime(2026, 7, 17, 13, 45, tzinfo=UTC))
        self.assertEqual(rows[0]["ticker"], "AAPL")
        self.assertTrue(meta["complete_universe"])
        self.assertTrue(meta["materialized"])
        self.assertEqual(meta["source_revision"], "7:1200:2026-07-17 14:00:00")
        insert = next(sql for sql in FakeClient.calls if "INSERT INTO" in sql)
        self.assertIn("FROM market_sip_compact.events_2026", insert)
        self.assertIn("GROUP BY ticker", insert)
        self.assertNotIn("ticker IN", insert)

    def test_reference_projection_is_one_causal_tradable_universe_query(self) -> None:
        class ReferenceClient:
            calls: list[str] = []

            def __init__(self, *_args) -> None:
                pass

            def execute(self, sql: str, **_kwargs) -> str:
                self.calls.append(sql)
                return '{"ticker":"AAPL","company_name":"APPLE INC","country":"US","market_cap":4374000000000,"float_shares":14400000000,"short_interest":144248000,"short_crowding_pct":1.0017,"days_to_cover":2.76,"logo_relative_path":"branding/logo/aapl.svg"}\n'

        with patch("src.backend.historical_scanner_service.ClickHouseHttpClient", ReferenceClient):
            rows = historical_scanner_reference_projection(datetime(2026, 7, 17, 13, 45, tzinfo=UTC))

        self.assertEqual(rows["AAPL"]["company_name"], "APPLE INC")
        self.assertEqual(rows["AAPL"]["country"], "US")
        self.assertEqual(rows["AAPL"]["logo_url"], "/api/real-live-trading/logo?path=branding%2Flogo%2Faapl.svg")
        self.assertAlmostEqual(rows["AAPL"]["short_crowding_pct"], 1.0017)
        self.assertEqual(len(ReferenceClient.calls), 1)
        query = ReferenceClient.calls[0]
        self.assertIn("is_tradable = 1", query)
        self.assertIn("inserted_at <= cutoff", query)
        self.assertIn("published_at_utc", query)
        self.assertIn("coalesce(scanner.logo_asset_id, current_branding.logo_asset_id, i.logo_asset_id)", query)
        self.assertNotIn("ticker IN", query)


if __name__ == "__main__":
    unittest.main()
