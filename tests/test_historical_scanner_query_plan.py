from __future__ import annotations

import unittest
from datetime import UTC, datetime

from src.backend.application_registry import QUERY_PLANS
from src.backend.query_plans.historical_scanner_materialization_v1 import (
    QUERY_PLAN_ID,
    QUERY_PLAN_VERSION,
    scanner_snapshot_materialization,
    source_revision_query,
    technical_snapshot_materialization,
)


class HistoricalScannerQueryPlanTests(unittest.TestCase):
    def test_plan_registers_event_revision_and_daily_bar_sources(self) -> None:
        plan = next(row for row in QUERY_PLANS if row.plan_id == QUERY_PLAN_ID)
        self.assertEqual(plan.version, QUERY_PLAN_VERSION)
        self.assertEqual(
            set(plan.source_paths),
            {
                "market_sip_compact.events_YYYY",
                "market_sip_compact.events_ordinal_continuity",
                "market_sip_compact.daily_session_bars_by_symbol_time_v1",
            },
        )

    def test_technical_plan_is_window_bounded_and_uses_causal_daily_baseline(self) -> None:
        query = technical_snapshot_materialization(
            source_database="market_sip_compact",
            table_prefix="events_",
            snapshot_at=datetime(2026, 1, 2, 15, 0, tzinfo=UTC),
            window_start=datetime(2025, 12, 31, 14, 0, tzinfo=UTC),
            calculation_window="extended_session",
            source_revision="revision-7",
        )
        self.assertIn("market_sip_compact.events_2025", query)
        self.assertIn("market_sip_compact.events_2026", query)
        self.assertIn("sip_timestamp_us >=", query)
        self.assertIn("sip_timestamp_us <", query)
        self.assertIn("available_at_us <=", query)
        self.assertIn("LIMIT 20 BY sym", query)
        self.assertIn("'revision-7'", query)

    def test_core_scanner_plan_keeps_only_registered_primitives(self) -> None:
        query = scanner_snapshot_materialization(
            source_database="market_sip_compact",
            table_prefix="events_",
            snapshot_at=datetime(2026, 8, 11, 15, 0, tzinfo=UTC),
            window_start=datetime(2026, 8, 11, 14, 30, tzinfo=UTC),
            lookback_minutes=30,
            source_revision="revision-9",
        )
        self.assertIn("last_price", query)
        self.assertIn("change_5m_pct", query)
        self.assertIn("volume", query)
        self.assertIn("trade_count", query)
        self.assertIn("quote_count", query)
        self.assertNotIn("relative_volume", query)
        self.assertNotIn("GenericStructure", query)

    def test_revision_query_uses_canonical_latest_continuity_rows(self) -> None:
        query = source_revision_query(
            database="market_sip_compact",
            snapshot_at=datetime(2026, 8, 11, 15, 0, tzinfo=UTC),
        )
        self.assertIn("argMax(event_count, tuple(build_step, updated_at))", query)
        self.assertIn("source_date = toDate('2026-08-11')", query)
        self.assertIn("GROUP BY ticker", query)


if __name__ == "__main__":
    unittest.main()
