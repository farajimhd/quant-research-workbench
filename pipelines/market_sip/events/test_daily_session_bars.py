from __future__ import annotations

import argparse
import datetime as dt
import io
import unittest
from pathlib import Path

from rich.console import Console

from pipelines.market_sip.events.clickhouse_build_daily_session_bars import (
    create_target_table_sql,
    insert_session_bars_sql,
    SessionBuildReporter,
)
from pipelines.market_sip.events.session_bar_contract import FEATURE_NAMES, session_table_columns
from research.bar_gpt.v1.build_daily_sessions_from_adjusted_1s import insert_sql as adjusted_rollup_sql


def args(**overrides: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "database": "market_sip_compact",
        "events_table_base": "events",
        "source_table": "bar_gpt_1s_bars_v2_cohort_2tb_split_adjusted",
        "target_table": "daily_session_bars_by_symbol_time_v1",
        "identity_database": "q_live",
        "symbol_interval_table": "id_symbol_interval_v1",
        "ticker_entity_table": "market_ticker_event_entity_v1",
        "storage_policy": "live_market_ssd",
        "max_threads": 4,
        "max_memory_usage": "8G",
        "max_bytes_before_external_group_by": "2G",
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class DailySessionContractTest(unittest.TestCase):
    def test_schema_is_wide_composable_geometry_with_adjustment_provenance(self) -> None:
        columns = dict(session_table_columns())
        self.assertEqual(set(FEATURE_NAMES) - set(columns), set())
        self.assertIn("trade_price_size_sum", columns)
        self.assertIn("spread_squared_sum", columns)
        self.assertIn("queue_imbalance_sum", columns)
        self.assertIn("source_ticker", columns)
        self.assertIn("canonical_ticker", columns)
        self.assertIn("adjustment_asof_date", columns)
        ddl = create_target_table_sql(args())
        self.assertIn("ORDER BY (source_ticker, session_date, session_kind)", ddl)
        self.assertIn("storage_policy = 'live_market_ssd'", ddl)

    def test_raw_builder_emits_three_explicit_sessions_and_q_live_identity(self) -> None:
        sql = insert_session_bars_sql(args(), dt.date(2026, 7, 1), dt.date(2026, 7, 8))
        self.assertIn("['premarket', 'regular', 'after_hours']", sql)
        self.assertIn("`q_live`.`id_symbol_interval_v1` FINAL", sql)
        self.assertIn("ordered_sip_events_unadjusted", sql)
        self.assertIn("toUInt8(0) AS adjusted", sql)
        self.assertIn("ASOF LEFT JOIN identity_starts", sql)
        self.assertIn("argMax(provider_entity_key", sql)
        self.assertIn("ambiguous_source_ticker", sql)
        self.assertEqual(sql.count("FROM `market_sip_compact`.`events_2026`"), 1)

    def test_model_daily_rollup_uses_adjusted_one_second_authority(self) -> None:
        sql = adjusted_rollup_sql(args(target_table="bar_gpt_daily_sessions_v3_sip_adjusted"), dt.date(2026, 7, 1), dt.date(2026, 8, 1))
        self.assertIn("bar_gpt_1s_bars_v2_cohort_2tb_split_adjusted", sql)
        self.assertIn("bar_gpt_1s_split_adjusted_v2_rollup", sql)
        self.assertIn("toUInt8(1) AS adjusted", sql)
        self.assertIn("any(split_schedule_sha256)", sql)
        self.assertIn("any(adjustment_asof_date)", sql)
        self.assertIn("ASOF LEFT JOIN identity_starts", sql)
        self.assertIn("ambiguous_source_ticker", sql)

    def test_compact_terminal_retains_state_progress_and_evidence(self) -> None:
        reporter = SessionBuildReporter(
            argparse.Namespace(progress_layout="rich"),
            100,
            Path(r"D:\TradingML\runtimes\market_sip\daily_session_bars_v1\smoke\build.jsonl"),
        )
        reporter.state = "building"
        reporter.current = "[2025-01-01, 2025-01-08)"
        reporter.completed = 42
        reporter.rows = 1_234_567
        reporter.source_events = 98_765_432
        reporter.message = "aggregating ordered SIP events"
        output = io.StringIO()
        Console(file=output, width=64, height=22, force_terminal=False, color_system=None).print(reporter.render())
        compact = " ".join(output.getvalue().split())
        self.assertIn("building", compact)
        self.assertIn("42/100", compact)
        self.assertIn("98,765,432", compact)
        self.assertIn("build.jsonl", compact)


if __name__ == "__main__":
    unittest.main()
