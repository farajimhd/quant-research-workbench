from __future__ import annotations

from datetime import UTC, datetime
import unittest
from unittest.mock import patch

from src.backend.ticker_facts_service import (
    aggregate_daily_volume,
    aggregate_short_volume,
    company_country_code,
    daily_volume_history_points,
    identity_anchor_sql,
    metric_changes,
    normalize_ticker,
    parse_as_of,
    ratio_percent,
    select_fundamentals,
    short_interest_sql,
    short_volume_sql,
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
