from __future__ import annotations

from datetime import UTC, datetime
import unittest
from unittest.mock import patch

from src.backend.ticker_facts_service import (
    aggregate_daily_volume,
    aggregate_short_volume,
    build_health_timeline,
    company_country_code,
    daily_volume_history_points,
    identity_anchor_sql,
    metric_changes,
    normalize_ticker,
    parse_as_of,
    ratio_percent,
    rows_available_by,
    select_fundamentals,
    short_interest_sql,
    short_volume_sql,
    synthesize_stock_facts,
    ticker_facts_payload,
)


class TickerFactsServiceTest(unittest.TestCase):
    def test_ticker_and_clock_validation_are_explicit(self) -> None:
        self.assertEqual(normalize_ticker(" brk.b "), "BRK.B")
        self.assertEqual(parse_as_of("2026-07-14T09:45:00-04:00"), datetime(2026, 7, 14, 13, 45, tzinfo=UTC))
        with self.assertRaisesRegex(ValueError, "Ticker"):
            normalize_ticker("AAPL' OR 1=1")
        with self.assertRaisesRegex(ValueError, "timezone"):
            parse_as_of("2026-07-14T09:45:00")

    def test_identity_and_short_interest_queries_preserve_point_in_time_rules(self) -> None:
        cutoff = datetime(2026, 7, 14, 13, 45, tzinfo=UTC)
        identity = identity_anchor_sql("AAPL", cutoff, "q_live")
        short_interest = short_interest_sql("symbol:aapl", cutoff, "q_live")
        self.assertIn("inserted_at <= parseDateTime64BestEffort", identity)
        self.assertIn("u.currency_code = 'USD' DESC", identity)
        self.assertIn("u.product_type = 'STK' DESC", identity)
        self.assertIn("LIMIT 1 BY settlement_date", short_interest)
        self.assertIn("settlement_date <= toDate", short_interest)
        self.assertIn("LIMIT 1 BY trade_date", short_volume_sql("symbol:aapl", cutoff, "q_live"))

    def test_reference_store_failure_has_a_stable_service_error(self) -> None:
        with patch("src.backend.ticker_facts_service.ClickHouseHttpClient.execute", side_effect=OSError("offline")):
            with self.assertRaisesRegex(RuntimeError, "reference storage"):
                ticker_facts_payload("AAPL", as_of="2026-07-14T09:45:00-04:00")

    def test_fundamental_selection_uses_tag_priority_without_inventing_values(self) -> None:
        rows = [
            {"tag": "Revenues", "value": 100.0},
            {"tag": "RevenueFromContractWithCustomerExcludingAssessedTax", "value": 120.0},
            {"tag": "Assets", "value": 300.0},
        ]
        selected = select_fundamentals(rows)
        self.assertEqual(selected[0]["label"], "Revenue")
        self.assertEqual(selected[0]["value"], 120.0)
        self.assertEqual(selected[1], {"label": "Assets", "tag": "Assets", "value": 300.0})
        self.assertEqual(ratio_percent(10.0, 200.0), 5.0)
        self.assertIsNone(ratio_percent(10.0, None))

    def test_daily_and_short_volume_snapshots_preserve_prior_comparisons(self) -> None:
        daily = [
            {"session_date": "2026-07-17", "close": 11, "size_sum": 200},
            {"session_date": "2026-07-16", "close": 10, "size_sum": 100},
        ]
        current = aggregate_daily_volume(daily)
        previous = aggregate_daily_volume(daily, 1)
        self.assertEqual(current["latest_volume"], 200)
        self.assertEqual(current["relative_volume_20d"], 200 / 150)
        self.assertEqual(previous["latest_volume"], 100)
        self.assertEqual(daily_volume_history_points(daily, relative=True)[-1]["value"], current["relative_volume_20d"])
        short = [
            {"trade_date": "2026-07-17", "short_volume": 40, "total_volume": 100, "short_volume_ratio": 0.4},
            {"trade_date": "2026-07-16", "short_volume": 30, "total_volume": 100, "short_volume_ratio": 0.3},
        ]
        self.assertEqual(aggregate_short_volume(short)["ratio_20d"], 0.35)
        self.assertEqual(aggregate_short_volume(short, 1)["latest_short_volume_ratio"], 0.3)

    def test_metric_changes_report_numeric_direction_without_directional_advice(self) -> None:
        changes = metric_changes(
            market_rows=[
                {"market_cap": 120, "share_class_shares_outstanding": 12, "observed_at_utc": "2026-07-17"},
                {"market_cap": 100, "share_class_shares_outstanding": 10, "observed_at_utc": "2026-07-16"},
            ],
            float_rows=[],
            short_interest_rows=[],
            short_volume_rows=[],
            borrow_rows=[],
            volume_rows=[],
            fundamental_rows=[],
        )
        self.assertEqual(changes["market_cap"]["direction"], "up")
        self.assertEqual(changes["market_cap"]["delta"], 20)
        self.assertEqual(changes["shares_outstanding"]["previous"], 10)

    def test_company_country_prefers_domicile_and_uses_known_us_incorporation_codes(self) -> None:
        self.assertEqual(company_country_code({"domicile_country_code": "gb", "state_of_incorporation": "CA"}), "GB")
        self.assertEqual(company_country_code({"domicile_country_code": None, "state_of_incorporation": "CA"}), "US")
        self.assertIsNone(company_country_code({"domicile_country_code": None, "state_of_incorporation": "E9"}))

    def test_synthesis_reconciles_reported_and_estimated_float_without_replacing_reported_value(self) -> None:
        synthesis = synthesize_stock_facts(
            as_of=datetime(2026, 7, 17, tzinfo=UTC),
            borrow={"borrow_status": "shortable", "fee_rate": 1.0, "observed_at_utc": "2026-07-17"},
            fails_to_deliver={"fails_quantity": 10_000, "settlement_date": "2026-07-16"},
            float_rows=[{"effective_date": "2026-07-01", "free_float": 90_000_000, "shares_outstanding": 100_000_000}],
            fundamental_rows=self._fundamental_rows(),
            market_rows=[{"market_cap": 1_000_000_000, "observed_at_utc": "2026-07-01", "share_class_shares_outstanding": 100_000_000}],
            reg_sho={"threshold_status": "inactive", "threshold_date": "2026-07-16"},
            short_interest={"short_interest": 1_000_000, "days_to_cover": 1.0, "settlement_date": "2026-06-30"},
            short_volume={"ratio_20d": 0.42, "latest_trade_date": "2026-07-16"},
            split_rows=[],
            volume_rows=self._volume_rows(),
        )
        supply = synthesis["cards"][0]
        self.assertEqual(supply["value"], 90_000_000)
        self.assertEqual(supply["reported_value"], 90_000_000)
        self.assertEqual(supply["estimated_value"], 95_000_000)
        self.assertEqual(supply["reconciliation"], "aligned")
        self.assertEqual({row["label"] for row in supply["evidence"]}, {"Reported free float", "SEC-implied float", "Shares outstanding", "Market-cap-implied shares"})

    def test_short_crowding_is_a_color_coded_auditable_decision(self) -> None:
        synthesis = synthesize_stock_facts(
            as_of=datetime(2026, 7, 17, tzinfo=UTC),
            borrow={"borrow_status": "hard_to_borrow", "fee_rate": 18.0, "observed_at_utc": "2026-07-17"},
            fails_to_deliver={"fails_quantity": 500_000, "settlement_date": "2026-07-16"},
            float_rows=[{"effective_date": "2026-07-01", "free_float": 90_000_000, "shares_outstanding": 100_000_000}],
            fundamental_rows=self._fundamental_rows(),
            market_rows=[],
            reg_sho={"threshold_status": "active", "threshold_date": "2026-07-16"},
            short_interest={"short_interest": 12_000_000, "days_to_cover": 6.0, "settlement_date": "2026-06-30"},
            short_volume={"ratio_20d": 0.55, "latest_trade_date": "2026-07-16"},
            split_rows=[],
            volume_rows=self._volume_rows(),
        )
        crowding = next(card for card in synthesis["cards"] if card["id"] == "short_crowding")
        self.assertIn(crowding["label"], {"High", "Extreme"})
        self.assertEqual(crowding["tone"], "negative")
        self.assertEqual(len(crowding["decision_inputs"]), 6)
        self.assertAlmostEqual(crowding["value"], 13.3333333333)

    def test_health_history_uses_source_availability_dates_without_backpainting(self) -> None:
        late_backfill = {"effective_date": "2025-01-15", "inserted_at": "2026-07-01", "free_float": 90_000_000}
        self.assertEqual(rows_available_by([late_backfill], datetime(2025, 2, 1).date(), "float"), [late_backfill])
        future_filing = {"period_end_date": "2024-12-31", "filed_at_utc": "2025-03-01", "value": 10.0}
        self.assertEqual(rows_available_by([future_filing], datetime(2025, 2, 1).date(), "fundamentals"), [])

        results = {
            "volume": [
                {"session_date": "2025-01-31", "bar_end": "2025-01-31 20:00:00", "close": 10.0, "size_sum": 1_000_000},
                {"session_date": "2025-02-28", "bar_end": "2025-02-28 20:00:00", "close": 11.0, "size_sum": 1_000_000},
            ],
            "fundamentals": [future_filing],
        }
        points = build_health_timeline(results, datetime(2025, 2, 28, tzinfo=UTC))
        self.assertEqual(points, [], "A later filing must not be projected backward into January or February health.")

    @staticmethod
    def _volume_rows() -> list[dict[str, object]]:
        return [
            {"session_date": f"2026-06-{day:02d}", "bar_end": f"2026-06-{day:02d} 20:00:00", "close": 10.0, "size_sum": 1_000_000}
            for day in range(30, 9, -1)
        ]

    @staticmethod
    def _fundamental_rows() -> list[dict[str, object]]:
        return [
            {"tag": "EntityPublicFloat", "value": 950_000_000, "period_end_date": "2026-06-30", "filed_at_utc": "2026-07-01", "fiscal_period": "FY"},
            {"tag": "EntityCommonStockSharesOutstanding", "value": 100_000_000, "period_end_date": "2026-06-30", "filed_at_utc": "2026-07-01", "fiscal_period": "FY"},
            {"tag": "EntityCommonStockSharesOutstanding", "value": 103_000_000, "period_end_date": "2025-06-30", "filed_at_utc": "2025-07-01", "fiscal_period": "FY"},
            {"tag": "Revenues", "value": 1_200_000_000, "period_end_date": "2026-06-30", "filed_at_utc": "2026-07-01", "fiscal_period": "FY", "form_type": "10-K"},
            {"tag": "Revenues", "value": 1_000_000_000, "period_end_date": "2025-06-30", "filed_at_utc": "2025-07-01", "fiscal_period": "FY", "form_type": "10-K"},
            {"tag": "NetIncomeLoss", "value": 180_000_000, "period_end_date": "2026-06-30", "filed_at_utc": "2026-07-01", "fiscal_period": "FY", "form_type": "10-K"},
            {"tag": "NetIncomeLoss", "value": 150_000_000, "period_end_date": "2025-06-30", "filed_at_utc": "2025-07-01", "fiscal_period": "FY", "form_type": "10-K"},
            {"tag": "OperatingIncomeLoss", "value": 200_000_000, "period_end_date": "2026-06-30", "filed_at_utc": "2026-07-01", "fiscal_period": "FY", "form_type": "10-K"},
            {"tag": "NetCashProvidedByUsedInOperatingActivities", "value": 220_000_000, "period_end_date": "2026-06-30", "filed_at_utc": "2026-07-01", "fiscal_period": "FY", "form_type": "10-K"},
            {"tag": "PaymentsToAcquirePropertyPlantAndEquipment", "value": 40_000_000, "period_end_date": "2026-06-30", "filed_at_utc": "2026-07-01", "fiscal_period": "FY", "form_type": "10-K"},
            {"tag": "CashAndCashEquivalentsAtCarryingValue", "value": 300_000_000, "period_end_date": "2026-06-30", "filed_at_utc": "2026-07-01"},
            {"tag": "Liabilities", "value": 500_000_000, "period_end_date": "2026-06-30", "filed_at_utc": "2026-07-01"},
            {"tag": "LongTermDebtNoncurrent", "value": 100_000_000, "period_end_date": "2026-06-30", "filed_at_utc": "2026-07-01"},
            {"tag": "EarningsPerShareDiluted", "value": 1.8, "period_end_date": "2026-06-30", "filed_at_utc": "2026-07-01", "fiscal_period": "FY", "form_type": "10-K"},
        ]
