from __future__ import annotations

from datetime import UTC, date, datetime
import unittest

from src.backend.daily_session_bars import daily_session_trade_bars_relation_sql
from src.backend.query_plans.market_daily_bars_v1 import (
    daily_market_reference_projection,
    daily_session_trade_bars,
)


class DailySessionBarsTests(unittest.TestCase):
    def test_daily_session_relation_is_complete_causal_and_identity_safe(self) -> None:
        sql = daily_session_trade_bars_relation_sql(
            database="market_sip_compact",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 7, 15),
            as_of=datetime(2026, 7, 14, 20, 0, tzinfo=UTC),
            ticker="META",
        )

        self.assertIn("daily_session_bars_by_symbol_time_v1", sql)
        self.assertIn("canonical_ticker = 'META'", sql)
        self.assertIn("identity_status != 'ambiguous_source_ticker'", sql)
        self.assertIn("available_at_us <=", sql)
        self.assertIn("uniqExact(session_kind) = 3", sql)
        self.assertIn("sum(trade_event_count) AS event_count", sql)

    def test_daily_session_relation_rejects_noncausal_inputs(self) -> None:
        with self.assertRaisesRegex(ValueError, "timezone"):
            daily_session_trade_bars_relation_sql(
                database="market_sip_compact",
                start_date=date(2026, 1, 1),
                end_date=date(2026, 1, 2),
                as_of=datetime(2026, 1, 2),
            )

    def test_compatibility_import_is_the_registered_plan_builder(self) -> None:
        self.assertIs(daily_session_trade_bars_relation_sql, daily_session_trade_bars)


    def test_watchlist_reference_projection_reuses_causal_daily_plan(self) -> None:
        sql = daily_market_reference_projection(
            database="market_sip_compact",
            start_date=date(2026, 8, 1),
            end_date=date(2026, 8, 11),
            as_of=datetime(2026, 8, 11, 12, 0, tzinfo=UTC),
        )

        self.assertIn("argMax(close, session_date) AS previous_close", sql)
        self.assertIn("argMax(session_date, session_date) AS previous_session_date", sql)
        self.assertIn("avg(size_sum) AS average_daily_volume", sql)
        self.assertIn("available_at_us <=", sql)
        self.assertIn("identity_status != 'ambiguous_source_ticker'", sql)
