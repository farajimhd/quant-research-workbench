from __future__ import annotations

import unittest
import datetime as dt
import json
import tempfile
import threading
from collections import deque
from pathlib import Path

from rich.console import Console

from research.bar_gpt.v3.cohort import BAR_GPT_TRAINING_TICKERS
from research.bar_gpt.v3.audit_offline_shards import (
    discover_sidecars,
    parse_args as parse_audit_args,
)
from research.bar_gpt.v3.config import DataConfig
from research.bar_gpt.v3.direct_event_shards import (
    DirectEventShardDataset,
    _is_event_authority_boundary,
    _iter_prefetched_pages_in_order,
    calendar_lookback_days,
    direct_event_preflight,
    direct_trade_bar_query,
)
from research.bar_gpt.v3.loader import ClickHouseBarStreamConfig, TickerInterval
from research.bar_gpt.v3.offline_shards import (
    OFFLINE_SHARD_CONTRACT_VERSION,
    ShardBuildReporter,
    build_data_config,
    parse_args as parse_offline_args,
)
from research.bar_gpt.v3.run_build_offline_dataset import (
    DATASET_END_DATE,
    DATASET_START_DATE,
    EXPECTED_UNITS,
    certify_complete_catalog,
    commands as full_commands,
    parse_args as parse_full_args,
)
from research.bar_gpt.v3.run_build_offline_shards import parse_args as parse_build_launcher_args
from research.bar_gpt.v3.run_pilot_offline_shards import commands as pilot_commands
from research.bar_gpt.v3.run_pilot_offline_shards import parse_args as parse_pilot_args


class DirectEventShardContractTest(unittest.TestCase):
    def test_resume_replays_certified_units_as_state_only_history(self) -> None:
        config = DataConfig(
            tickers=("AAPL",),
            start_date="2019-01-01",
            end_date="2019-03-01",
            validation_start_date="2019-02-01",
            validation_slices=(("AAPL", "2019-02-01", "2019-03-01"),),
        )
        dataset = DirectEventShardDataset(
            data_config=config,
            stream_config=ClickHouseBarStreamConfig(
                url="http://localhost:8123", user="default", password=""
            ),
            split="cache",
            seed=17,
            unit_tickers=("AAPL",),
            skip_unit_keys=frozenset(("AAPL:2019-01",)),
        )

        self.assertEqual(
            [unit.start_date for _index, unit in dataset._units()],
            ["2019-01-01", "2019-02-01"],
        )
        self.assertEqual(dataset.state_only_unit_keys, frozenset(("AAPL:2019-01",)))

    def test_preflight_retains_event_empty_ticker_with_zero_scheduler_weight(self) -> None:
        config = DataConfig(
            tickers=("AAPL", "ARM"),
            start_date="2019-01-01",
            end_date="2022-01-01",
        )

        class Client:
            def execute(self, query: str) -> str:
                if "FROM system.tables" in query:
                    return "\n".join((
                        "events_2019", "events_2020", "events_2021",
                        "events_ticker_day_index", "events_source_day_stats",
                        config.condition_reference_table,
                    ))
                if "groupUniqArray(source_filter_key)" in query:
                    return "['drop_trade_correction_codes=07,08,10,11|condition_slots=5']"
                if "sum(event_count)" in query:
                    return "AAPL\t12345\n"
                raise AssertionError(f"unexpected preflight query: {query}")

        _evidence, weights = direct_event_preflight(
            Client(), config, ("AAPL", "ARM"),
        )

        self.assertEqual(weights, {"AAPL": 12345, "ARM": 0})

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
        self.assertIn("block total discovered during source aggregation", rendered)
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
        # Condition-only sparse seconds are retained for condition context but
        # are not eligible trade seconds, so the daily count must sum the
        # explicit origin flag rather than count every sparse row.
        self.assertIn("toUInt64(sum(s.origin_eligible)) AS eligible_trade_second_count", daily_sql)

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

        boundary = dict(pilot_commands(parse_pilot_args([
            "--start-date", "2019-01-01", "--end-date", "2019-02-01",
        ])))["automatic complete pilot audit"]
        self.assertNotIn("--require-calendar-context", boundary)
        self.assertIn("--require-calendar-context", audit)

    def test_audit_partial_date_interval_selects_overlapping_shard_months(self) -> None:
        args = parse_audit_args([
            "--start-date", "2021-01-25", "--end-date", "2021-02-01",
        ])
        self.assertEqual(args.start_date, "2021-01-25")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for month in ("2020-12", "2021-01", "2021-02", "2021-03"):
                path = root / "tickers" / "AMC" / month[:4] / f"{month}.json"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({
                    "status": "complete", "unit_key": f"AMC:{month}",
                }), encoding="utf-8")

            january = discover_sidecars(
                root, tickers=("AMC",), start_date="2021-01-25",
                end_date="2021-02-01", limit=10,
            )
            self.assertEqual({path.stem for path in january}, {"2021-01"})

            crossing = discover_sidecars(
                root, tickers=("AMC",), start_date="2021-01-31",
                end_date="2021-02-02", limit=10,
            )
            self.assertEqual({path.stem for path in crossing}, {"2021-01", "2021-02"})

            crossing_year = discover_sidecars(
                root, tickers=("AMC",), start_date="2020-12-31",
                end_date="2021-01-02", limit=10,
            )
            self.assertEqual({path.stem for path in crossing_year}, {"2020-12", "2021-01"})

    def test_calendar_lookback_is_derived_from_configured_daily_warmup(self) -> None:
        self.assertEqual(calendar_lookback_days(DataConfig(calendar_warmup_daily_bars=500)), 750)

    def test_direct_builder_documents_authority_boundary_without_weakening_context(self) -> None:
        config = DataConfig(daily_history_start_date="2019-01-01")
        self.assertTrue(_is_event_authority_boundary(config, "2019-01-01"))
        self.assertFalse(_is_event_authority_boundary(config, "2019-02-01"))

    def test_full_launcher_uses_32_workers_and_resolvable_cohort(self) -> None:
        stages = full_commands(parse_full_args(["--workers", "32"]))
        self.assertEqual(len(stages), 3)
        build = dict(stages)["single 2019-2026 direct-event shard pass"]
        self.assertEqual(build[build.index("--workers") + 1], "32")
        self.assertEqual(build[build.index("--source-mode") + 1], "direct_events")
        self.assertEqual(build[build.index("--clickhouse-max-threads-per-worker") + 1], "2")
        self.assertEqual(build[build.index("--progress-layout") + 1], "rich")
        self.assertEqual(build[build.index("--start-date") + 1], "2019-01-01")
        self.assertEqual(build[build.index("--end-date") + 1], "2026-08-01")
        self.assertEqual(
            tuple(build[build.index("--tickers") + 1].split(",")),
            BAR_GPT_TRAINING_TICKERS,
        )

    def test_full_launcher_locks_only_the_exact_complete_unit_set(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "manifest"
            manifest.mkdir()
            units = [
                {"unit_key": f"{ticker}:{year:04d}-{month:02d}"}
                for ticker in BAR_GPT_TRAINING_TICKERS
                for year in range(2019, 2027)
                for month in range(1, 13)
                if f"{year:04d}-{month:02d}-01" < DATASET_END_DATE
            ]
            self.assertEqual(len(units), EXPECTED_UNITS)
            (manifest / "build_plan.json").write_text(json.dumps({
                "contract_version": OFFLINE_SHARD_CONTRACT_VERSION,
                "config_hash": "abc123",
                "selection": {
                    "tickers": list(BAR_GPT_TRAINING_TICKERS),
                    "start_date": DATASET_START_DATE,
                    "end_date": DATASET_END_DATE,
                },
                "planned_units": EXPECTED_UNITS,
            }), encoding="utf-8")
            catalog_path = manifest / "catalog.json"
            catalog = {
                "contract_version": OFFLINE_SHARD_CONTRACT_VERSION,
                "config_hash": "abc123",
                "counts": {
                    "units": EXPECTED_UNITS,
                    "complete": EXPECTED_UNITS,
                    "covered_empty": 0,
                    "bytes": 1,
                },
                "units": units,
            }
            catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
            self.assertEqual(certify_complete_catalog(root)["counts"]["units"], EXPECTED_UNITS)
            catalog["units"] = catalog["units"][:-1]
            catalog["counts"]["units"] -= 1
            catalog["counts"]["complete"] -= 1
            catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "incomplete catalog"):
                certify_complete_catalog(root)


if __name__ == "__main__":
    unittest.main()
