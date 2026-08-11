from __future__ import annotations

import unittest

from research.bar_gpt.v1.cohort import BAR_GPT_TRAINING_TICKERS
from research.bar_gpt.v1.config import DataConfig
from research.bar_gpt.v1.direct_event_shards import direct_trade_bar_query
from research.bar_gpt.v1.loader import ClickHouseBarStreamConfig, TickerInterval
from research.bar_gpt.v1.run_build_offline_dataset import commands as full_commands
from research.bar_gpt.v1.run_build_offline_dataset import parse_args as parse_full_args
from research.bar_gpt.v1.run_pilot_offline_shards import commands as pilot_commands
from research.bar_gpt.v1.run_pilot_offline_shards import parse_args as parse_pilot_args


class DirectEventShardContractTest(unittest.TestCase):
    def test_sql_requires_eligible_trade_for_token_origin_and_context(self) -> None:
        config = DataConfig(
            tickers=("AAPL",),
            start_date="2026-01-01",
            end_date="2026-02-01",
            validation_start_date="2026-01-01",
            validation_slices=(("AAPL", "2026-01-01", "2026-02-01"),),
        )
        sql = direct_trade_bar_query(
            config,
            ClickHouseBarStreamConfig(url="http://localhost:8123", user="default", password=""),
            ticker="AAPL",
            start_date="2026-01-02",
            end_date="2026-01-03",
            source_intervals=(TickerInterval("AAPL", "AAPL", "2019-01-01", "9999-12-31"),),
        )
        self.assertIn("countIf(trade_origin_eligible) > 0) AS context_eligible", sql)
        self.assertIn("countIf(trade_origin_eligible) > 0) AS origin_eligible", sql)
        self.assertIn("HAVING eligible_trade_event_count>0", sql)
        self.assertNotIn("bar_gpt_trade_correction_overlay", sql)
        self.assertIn("FORMAT ArrowStream", sql)

    def test_pilot_builds_then_runs_complete_direct_source_audit(self) -> None:
        stages = dict(pilot_commands(parse_pilot_args(["--execute", "--workers", "32"])))
        build = stages["direct event-to-shard pilot"]
        audit = stages["automatic complete pilot audit"]
        self.assertEqual(build[build.index("--workers") + 1], "32")
        self.assertEqual(build[build.index("--source-mode") + 1], "direct_events")
        self.assertIn("--execute", build)
        self.assertIn("--verify-sha256", audit)
        self.assertIn("--require-calendar-context", audit)
        self.assertIn("--verify-direct-source", audit)

    def test_full_launcher_uses_32_workers_and_resolvable_cohort(self) -> None:
        stages = full_commands(parse_full_args(["--workers", "32"]))
        self.assertEqual(len(stages), 2)
        for _label, command in stages:
            self.assertEqual(command[command.index("--workers") + 1], "32")
            self.assertEqual(command[command.index("--source-mode") + 1], "direct_events")
            self.assertEqual(
                tuple(command[command.index("--tickers") + 1].split(",")),
                BAR_GPT_TRAINING_TICKERS,
            )


if __name__ == "__main__":
    unittest.main()
