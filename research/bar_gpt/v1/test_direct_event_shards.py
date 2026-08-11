from __future__ import annotations

import unittest
import datetime as dt
import threading
from collections import deque
from pathlib import Path

from rich.console import Console

from research.bar_gpt.v1.cohort import BAR_GPT_TRAINING_TICKERS
from research.bar_gpt.v1.config import DataConfig
from research.bar_gpt.v1.direct_event_shards import (
    _iter_prefetched_pages_in_order,
    calendar_lookback_days,
    direct_trade_bar_query,
)
from research.bar_gpt.v1.loader import ClickHouseBarStreamConfig, TickerInterval
from research.bar_gpt.v1.offline_shards import (
    ShardBuildReporter,
    build_data_config,
    parse_args as parse_offline_args,
)
from research.bar_gpt.v1.run_build_offline_dataset import commands as full_commands
from research.bar_gpt.v1.run_build_offline_dataset import parse_args as parse_full_args
from research.bar_gpt.v1.run_build_offline_shards import parse_args as parse_build_launcher_args
from research.bar_gpt.v1.run_pilot_offline_shards import commands as pilot_commands
from research.bar_gpt.v1.run_pilot_offline_shards import parse_args as parse_pilot_args


class DirectEventShardContractTest(unittest.TestCase):
    def test_reporter_shows_precompletion_stage_and_source_page_progress(self) -> None:
        reporter = ShardBuildReporter(
            total=2,
            completed=0,
            root=Path("D:/pilot"),
            workers=1,
            layout="rich",
            refresh=0.5,
            worker_totals=(2,),
            worker_block_totals=(0,),
        )
        reporter.refresh = lambda **_kwargs: None  # type: ignore[method-assign]
        reporter.event((
            "source_page", 0, "AAPL",
            {"kind": "stage", "phase": "identity metadata", "detail": "1 ticker(s)"},
        ))
        self.assertEqual(reporter.worker_state[0], ("identity metadata", "1 ticker(s)"))
        reporter.event((
            "source_page", 0, "AAPL",
            {
                "kind": "page", "phase": "calendar warmup", "left": "2024-01-01",
                "right": "2024-02-01", "completed": 3, "total": 25,
                "elapsed_seconds": 52.0,
            },
        ))
        self.assertEqual(reporter.source_pages_completed, 3)
        console = Console(record=True, width=120, force_terminal=False)
        reporter._console = console
        console.print(reporter._render())
        rendered = console.export_text()
        self.assertIn("3/25 pages", rendered)
        self.assertIn("calendar warmup", rendered)
        self.assertIn("total available after source aggregation", rendered)
        self.assertNotIn("0/1", rendered)

    def test_prefetch_reports_completed_pages_immediately_but_yields_in_order(self) -> None:
        first_release = threading.Event()
        first = dt.date(2026, 1, 1)
        second = dt.date(2026, 2, 1)
        third = dt.date(2026, 3, 1)
        callbacks: list[str] = []

        def read_page(left: dt.date, _right: dt.date) -> list[str]:
            if left == first and not first_release.wait(timeout=1.0):
                raise AssertionError("later completed page was not reported promptly")
            return [left.isoformat()]

        def on_page(
            left: str,
            _right: str,
            _rows: int,
            _seconds: float,
            completed: int,
            _total: int,
            _elapsed: float,
        ) -> None:
            if completed == 0:
                return
            callbacks.append(left)
            if left == second.isoformat():
                first_release.set()

        yielded = list(_iter_prefetched_pages_in_order(
            deque(((first, second), (second, third))),
            depth=2,
            read_page=read_page,
            page_callback=on_page,
            thread_name_prefix="test-direct-pages",
        ))

        self.assertEqual(callbacks[0], second.isoformat())
        self.assertEqual(yielded, [[first.isoformat()], [second.isoformat()]])

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

        daily_sql = direct_trade_bar_query(
            config,
            ClickHouseBarStreamConfig(url="http://localhost:8123", user="default", password=""),
            ticker="AAPL",
            start_date="2026-01-02",
            end_date="2026-02-01",
            source_intervals=(TickerInterval("AAPL", "AAPL", "2019-01-01", "9999-12-31"),),
            group_daily=True,
        )
        self.assertIn("GROUP BY local_date_value, second_start_us", daily_sql)
        self.assertIn("GROUP BY s.local_date\n", daily_sql)
        self.assertIn("toUInt64(count()) AS eligible_trade_second_count", daily_sql)

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
        self.assertEqual(build[build.index("--clickhouse-prefetch-pages") + 1], "16")
        self.assertEqual(build[build.index("--clickhouse-max-concurrent-pages") + 1], "32")
        self.assertEqual(build[build.index("--clickhouse-max-threads-per-worker") + 1], "2")
        self.assertEqual(build[build.index("--progress-layout") + 1], "rich")
        # Exercise the actual child parser, not only launcher construction.
        _launcher_args, forwarded = parse_build_launcher_args(build[4:])
        child_args = parse_offline_args(forwarded)
        self.assertEqual(build_data_config(child_args).clickhouse_max_threads_per_worker, 2)

    def test_calendar_lookback_is_derived_from_configured_daily_warmup(self) -> None:
        self.assertEqual(calendar_lookback_days(DataConfig(calendar_warmup_daily_bars=500)), 750)

    def test_full_launcher_uses_32_workers_and_resolvable_cohort(self) -> None:
        stages = full_commands(parse_full_args(["--workers", "32"]))
        self.assertEqual(len(stages), 2)
        for _label, command in stages:
            self.assertEqual(command[command.index("--workers") + 1], "32")
            self.assertEqual(command[command.index("--source-mode") + 1], "direct_events")
            self.assertEqual(command[command.index("--clickhouse-max-threads-per-worker") + 1], "2")
            self.assertEqual(
                tuple(command[command.index("--tickers") + 1].split(",")),
                BAR_GPT_TRAINING_TICKERS,
            )


if __name__ == "__main__":
    unittest.main()
