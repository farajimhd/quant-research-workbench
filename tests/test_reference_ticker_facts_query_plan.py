from __future__ import annotations

import unittest
from datetime import UTC, datetime

from src.backend.query_plans.reference_ticker_facts_v1 import (
    identity_anchor,
    reference_fact_queries,
)
from src.backend.ticker_facts_service import identity_anchor_sql


class ReferenceTickerFactsQueryPlanTests(unittest.TestCase):
    def test_identity_anchor_is_causal_and_keeps_compatibility_import(self) -> None:
        cutoff = datetime(2026, 8, 11, 15, 45, 12, 123456, tzinfo=UTC)
        sql = identity_anchor("AAPL", cutoff, "q_live")
        self.assertEqual(sql, identity_anchor_sql("AAPL", cutoff, "q_live"))
        self.assertIn("universe_date <= toDate('2026-08-11')", sql)
        self.assertIn("inserted_at <= parseDateTime64BestEffort('2026-08-11T15:45:12.123+00:00')", sql)
        self.assertIn("s.first_seen_at_utc <= parseDateTime64BestEffort", sql)
        self.assertIn("upper(u.ticker) = 'AAPL'", sql)
        self.assertIn("LIMIT 1", sql)

    def test_identity_anchor_rejects_naive_cutoff(self) -> None:
        with self.assertRaisesRegex(ValueError, "timezone"):
            identity_anchor("AAPL", datetime(2026, 8, 11), "q_live")

    def test_reference_bundle_is_bounded_causal_and_uses_daily_bar_authority(self) -> None:
        cutoff = datetime(2026, 8, 11, 15, 45, tzinfo=UTC)
        queries = reference_fact_queries(
            ticker="AAPL",
            context={
                "issuer_id": "issuer:aapl",
                "security_id": "security:aapl",
                "symbol_id": "symbol:aapl",
            },
            cutoff=cutoff,
            database="q_live",
            historical_database="market_sip_compact",
        )
        self.assertEqual(
            set(queries),
            {
                "borrow",
                "classifications",
                "corporate",
                "fails_to_deliver",
                "float",
                "identifiers",
                "market",
                "reg_sho",
                "short_interest",
                "short_volume",
                "splits",
                "volume",
            },
        )
        self.assertIn("inserted_at <= parseDateTime64BestEffort", queries["float"])
        self.assertIn("LIMIT 1 BY settlement_date", queries["short_interest"])
        self.assertIn("market_cash_dividend_v1 FINAL", queries["corporate"])
        self.assertIn("daily_session_bars_by_symbol_time_v1", queries["volume"])
        self.assertIn("available_at_us <=", queries["volume"])


if __name__ == "__main__":
    unittest.main()
