from datetime import UTC, datetime
import unittest
from unittest.mock import patch

from src.backend.historical_scanner_service import (
    historical_scanner_fundamental_projection,
    historical_scanner_reference_projection,
    historical_scanner_snapshot,
)


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

    def test_fundamental_projection_reuses_canonical_scores_in_one_causal_query(self) -> None:
        class FundamentalClient:
            calls: list[str] = []

            def __init__(self, *_args) -> None:
                pass

            def execute(self, sql: str, **_kwargs) -> str:
                self.calls.append(sql)
                return '{"ticker":"AAPL","tag":"RevenueFromContractWithCustomerExcludingAssessedTax","taxonomy":"us-gaap","unit_code":"USD","value":416160000000,"fiscal_year":2025,"fiscal_period":"FY","period_end_date":"2025-09-27","filed_at_utc":"2025-10-31 12:00:00","form_type":"10-K","accession_number":"0001","recorded_at_utc":"2025-10-31 12:01:00"}\n'

        analysis = {
            "coverage_percent": 100.0,
            "facets": [
                {"id": "profitability", "score": 95.0},
                {"id": "growth", "score": 57.0},
                {"id": "cash_quality", "score": 80.0},
                {"id": "balance_sheet", "score": 62.0},
                {"id": "capital_discipline", "score": 98.0},
            ],
            "label": "Strong",
            "metrics": [
                {"id": "operating_margin", "value": 32.0},
                {"id": "revenue_growth", "value": 6.43},
            ],
            "score": 78.0,
        }
        with (
            patch("src.backend.historical_scanner_service.ClickHouseHttpClient", FundamentalClient),
            patch("src.backend.historical_scanner_service.analyze_fundamentals", return_value=analysis),
            patch("src.backend.historical_scanner_service.financial_card_and_scores", return_value=({"value": 89.0, "label": "Strong"}, {"profitability": 92.0, "cash_generation": 100.0, "balance_sheet": 71.0})),
            patch("src.backend.historical_scanner_service.share_base_card", return_value=({"value": -1.66}, 77.0)),
            patch("src.backend.historical_scanner_service.valuation_card_from_facts", return_value={"value": 44.9, "label": "Very premium"}),
            patch("src.backend.historical_scanner_service.select_fundamentals", return_value=[{"label": "Revenue", "value": 416_160_000_000}]),
        ):
            rows = historical_scanner_fundamental_projection(
                datetime(2026, 7, 17, 13, 45, tzinfo=UTC),
                prices_by_ticker={"AAPL": 314.8},
            )

        self.assertEqual(rows["AAPL"]["xbrl_quality_score"], 78.0)
        self.assertEqual(rows["AAPL"]["xbrl_profitability_score"], 95.0)
        self.assertEqual(rows["AAPL"]["financial_trajectory_score"], 89.0)
        self.assertEqual(rows["AAPL"]["share_base_pressure_pct"], -1.66)
        self.assertEqual(rows["AAPL"]["valuation_pe"], 44.9)
        self.assertEqual(rows["AAPL"]["fundamental_operating_margin_pct"], 32.0)
        self.assertEqual(rows["AAPL"]["fundamental_revenue"], 416_160_000_000)
        self.assertEqual(rows["AAPL"]["fundamental_latest_filing_at"], "2025-10-31T12:00:00+00:00")
        self.assertEqual(len(FundamentalClient.calls), 1)
        query = FundamentalClient.calls[0]
        self.assertIn("INNER JOIN universe", query)
        self.assertIn("feature_tradable_universe_v1 AS u", query)
        self.assertIn("startsWith(u.issuer_id, 'issuer:cik:')", query)
        self.assertIn("replaceOne(u.issuer_id, 'issuer:cik:', '')", query)
        self.assertNotIn("id_sec_market_bridge_v3", query)
        self.assertIn("LIMIT 1 BY ticker, tag, period_end_date, fiscal_period, unit_code", query)
        self.assertIn("LIMIT 8 BY ticker, tag", query)
        self.assertIn("f.filed_at_utc <= cutoff", query)
        self.assertIn("f.recorded_at_utc <= cutoff", query)
        self.assertNotIn("ticker IN", query)


if __name__ == "__main__":
    unittest.main()
