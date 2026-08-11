from datetime import UTC, datetime
import unittest
from unittest.mock import patch

from src.backend.historical_scanner_service import (
    _latest_cached_rows,
    _qmd_snapshot_complete,
    historical_scanner_fundamental_projection,
    historical_scanner_qmd_projection,
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
    def test_latest_cached_snapshot_uses_a_non_conflicting_aggregate_alias(self) -> None:
        class LatestClient:
            def execute(self, sql: str, **_kwargs) -> str:
                if "maxOrNull(snapshot_at_utc)" in sql:
                    self.assert_query(sql)
                    return '{"latest_snapshot_at_utc":"2026-07-17 13:44:00.000"}\n'
                return ""

            @staticmethod
            def assert_query(sql: str) -> None:
                if "AS latest_snapshot_at_utc" not in sql or "AS snapshot_at_utc" in sql:
                    raise AssertionError(sql)

        rows, snapshot_at = _latest_cached_rows(
            LatestClient(),
            datetime(2026, 7, 17, 13, 45, tzinfo=UTC),
            15,
            "revision",
        )

        self.assertEqual(rows, [])
        self.assertEqual(snapshot_at, datetime(2026, 7, 17, 13, 44, tzinfo=UTC))

    def test_qmd_completion_rejects_empty_and_count_mismatched_artifacts(self) -> None:
        class CompletionClient:
            def __init__(self, indicator_count: int, stored_count: int) -> None:
                self.indicator_count = indicator_count
                self.stored_count = stored_count

            def execute(self, sql: str, **_kwargs) -> str:
                if "FROM q_live.canvas_historical_qmd_snapshot_meta_v1 FINAL" in sql:
                    return (
                        f'{{"complete":1,"indicator_count":{self.indicator_count}}}\n'
                    )
                return f'{{"indicator_count":{self.stored_count}}}\n'

        snapshot_at = datetime(2026, 7, 17, 13, 45, tzinfo=UTC)
        self.assertFalse(
            _qmd_snapshot_complete(CompletionClient(0, 0), snapshot_at, "revision")
        )
        self.assertFalse(
            _qmd_snapshot_complete(CompletionClient(2, 1), snapshot_at, "revision")
        )
        self.assertTrue(
            _qmd_snapshot_complete(CompletionClient(2, 2), snapshot_at, "revision")
        )

    def test_qmd_projection_materializes_canonical_indicators_and_ranked_signals(self) -> None:
        class QmdClient:
            calls: list[str] = []

            def __init__(self, *_args) -> None:
                pass

            def execute(self, sql: str, **_kwargs) -> str:
                self.calls.append(sql)
                if f"FROM q_live.canvas_historical_qmd_snapshot_meta_v1 FINAL" in sql:
                    return '{"complete":0}\n'
                if "SELECT ticker, indicator_json, active_signals_json" in sql:
                    return (
                        '{"ticker":"AAPL","indicator_json":"{\\"sym\\":\\"AAPL\\",'
                        '\\"timeframe\\":\\"10s\\",\\"bar_end\\":\\"2026-07-17T13:45:00Z\\",'
                        '\\"flow_structure_composite_score\\":0.7}",'
                        '"active_signals_json":"[{\\"event_id\\":\\"event-1\\",'
                        '\\"signal_id\\":\\"signal-1\\",\\"signal_key\\":'
                        '\\"price_volume_expansion\\",\\"ticker\\":\\"AAPL\\",'
                        '\\"working_timeframe\\":\\"1s\\",\\"state\\":\\"triggered\\",'
                        '\\"direction\\":\\"bullish\\",\\"score\\":0.8,'
                        '\\"rank_score\\":0.91,\\"confidence\\":0.85,'
                        '\\"effective_at\\":\\"2026-07-17T13:45:00Z\\"}]"}\n'
                    )
                if "SELECT event_json" in sql:
                    return (
                        '{"event_json":"{\\"event_id\\":\\"event-1\\",'
                        '\\"signal_id\\":\\"signal-1\\",\\"signal_key\\":'
                        '\\"price_volume_expansion\\",\\"ticker\\":\\"AAPL\\",'
                        '\\"working_timeframe\\":\\"1s\\",\\"state\\":\\"triggered\\",'
                        '\\"direction\\":\\"bullish\\",\\"score\\":0.8,'
                        '\\"rank_score\\":0.91,\\"confidence\\":0.85,'
                        '\\"effective_at\\":\\"2026-07-17T13:45:00Z\\"}"}\n'
                    )
                return ""

        payload = {
            "active_signals": [
                {
                    "event_id": "event-1",
                    "signal_id": "signal-1",
                    "signal_key": "price_volume_expansion",
                    "ticker": "AAPL",
                    "working_timeframe": "1s",
                    "state": "triggered",
                    "direction": "bullish",
                    "score": 0.8,
                    "rank_score": 0.91,
                    "confidence": 0.85,
                    "effective_at": "2026-07-17T13:45:00Z",
                }
            ],
            "engine_version": "qmd-market-signal-v3",
            "event_count": 1200,
            "indicators": [
                {
                    "sym": "AAPL",
                    "timeframe": "10s",
                    "bar_end": "2026-07-17T13:45:00Z",
                    "flow_structure_composite_score": 0.7,
                }
            ],
            "recent_signal_events": [],
            "schema_version": "canvas_historical_qmd_snapshot_v3",
            "source_revision": {"token": "7:1200:2026-07-17 14:00:00"},
        }
        with (
            patch("src.backend.historical_scanner_service.ClickHouseHttpClient", QmdClient),
            patch(
                "src.backend.historical_scanner_service.historical_scanner_derived_snapshot",
                return_value=payload,
            ),
        ):
            projection, signal_rows, meta = historical_scanner_qmd_projection(
                datetime(2026, 7, 17, 13, 45, tzinfo=UTC),
                source_revision="7:1200:2026-07-17 14:00:00",
            )

        self.assertEqual(projection["AAPL"]["indicator_type"], "qmd")
        self.assertEqual(projection["AAPL"]["flow_structure_composite_score"], 0.7)
        self.assertEqual(projection["AAPL"]["signal_type"], "price_volume_expansion")
        self.assertEqual(projection["AAPL"]["signal_rank_score"], 0.91)
        self.assertEqual(projection["AAPL"]["active_signal_count"], 1)
        self.assertEqual(signal_rows[0]["signal_event_id"], "event-1")
        self.assertEqual(meta["qmd_indicator_row_count"], 1)
        self.assertTrue(
            any(
                "INSERT INTO q_live.canvas_historical_qmd_snapshot_meta_v1" in sql
                for sql in QmdClient.calls
            )
        )

    def test_full_universe_snapshot_returns_cached_rows_while_exact_clock_builds(self) -> None:
        FakeClient.calls = []
        fallback_at = datetime(2026, 7, 17, 13, 44, tzinfo=UTC)
        fallback_rows = [
            {
                "symbol": "AAPL",
                "ticker": "AAPL",
                "last": 200,
                "change_pct": 1.5,
                "change_5m_pct": 0.4,
                "volume": 1000,
                "trade_count": 10,
                "quote_count": 20,
            }
        ]
        with (
            patch("src.backend.historical_scanner_service.ClickHouseHttpClient", FakeClient),
            patch(
                "src.backend.historical_scanner_service._latest_cached_rows",
                return_value=(fallback_rows, fallback_at),
            ),
            patch(
                "src.backend.historical_scanner_service._schedule_scanner_materialization",
                return_value="building",
            ) as schedule,
        ):
            rows, meta = historical_scanner_snapshot(datetime(2026, 7, 17, 13, 45, tzinfo=UTC))
        self.assertEqual(rows[0]["ticker"], "AAPL")
        self.assertTrue(meta["complete_universe"])
        self.assertFalse(meta["materialized"])
        self.assertEqual(meta["source_revision"], "7:1200:2026-07-17 14:00:00")
        self.assertEqual(meta["snapshot_at_utc"], fallback_at.isoformat())
        self.assertEqual(meta["status"], "refreshing")
        self.assertEqual(meta["refresh_status"], "building")
        schedule.assert_called_once()

    def test_full_universe_snapshot_reports_building_without_a_cached_baseline(self) -> None:
        with (
            patch("src.backend.historical_scanner_service.ClickHouseHttpClient", FakeClient),
            patch(
                "src.backend.historical_scanner_service._latest_cached_rows",
                return_value=([], None),
            ),
            patch(
                "src.backend.historical_scanner_service._schedule_scanner_materialization",
                return_value="building",
            ),
        ):
            rows, meta = historical_scanner_snapshot(
                datetime(2026, 7, 17, 13, 45, tzinfo=UTC)
            )

        self.assertEqual(rows, [])
        self.assertFalse(meta["complete_universe"])
        self.assertEqual(meta["status"], "building")
        self.assertEqual(meta["row_count"], 0)

    def test_reference_projection_is_one_causal_tradable_universe_query(self) -> None:
        class ReferenceClient:
            calls: list[str] = []

            def __init__(self, *_args) -> None:
                pass

            def execute(self, sql: str, **_kwargs) -> str:
                self.calls.append(sql)
                return '{"ticker":"AAPL","company_name":"APPLE INC","country":"US","market_cap":4374000000000,"float_shares":14400000000,"short_interest":144248000,"short_crowding_pct":1.0017,"days_to_cover":2.76,"ipo_days_to_event":12,"split_days_to_event":-3,"logo_relative_path":"branding/logo/aapl.svg"}\n'

        with patch("src.backend.historical_scanner_service.ClickHouseHttpClient", ReferenceClient):
            rows = historical_scanner_reference_projection(datetime(2026, 7, 17, 13, 45, tzinfo=UTC))

        self.assertEqual(rows["AAPL"]["company_name"], "APPLE INC")
        self.assertEqual(rows["AAPL"]["country"], "US")
        self.assertEqual(rows["AAPL"]["logo_url"], "/api/real-live-trading/logo?path=branding%2Flogo%2Faapl.svg")
        self.assertAlmostEqual(rows["AAPL"]["short_crowding_pct"], 1.0017)
        self.assertEqual(rows["AAPL"]["ipo_days_to_event"], 12)
        self.assertEqual(rows["AAPL"]["split_days_to_event"], -3)
        self.assertEqual(len(ReferenceClient.calls), 1)
        query = ReferenceClient.calls[0]
        self.assertIn("is_tradable = 1", query)
        self.assertIn("inserted_at <= cutoff", query)
        self.assertIn("published_at_utc", query)
        self.assertIn("FROM q_live.market_ipo_v1 FINAL", query)
        self.assertIn("FROM q_live.market_stock_split_v1 FINAL", query)
        self.assertIn("if(empty(ipo.symbol_id), NULL, dateDiff('day', cutoff_date, ipo.listing_date))", query)
        self.assertIn("if(empty(split.symbol_id), NULL, dateDiff('day', cutoff_date, split.execution_date))", query)
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
