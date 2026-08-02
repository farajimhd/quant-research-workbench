from __future__ import annotations

import argparse
import datetime as dt
import io
import unittest
from pathlib import Path

from rich.console import Console

from research.bar_gpt.v1.build_daily_context import (
    CONTRACT_SHA256,
    DailyBootstrapReporter,
    create_manifest_table_sql,
    create_target_table_sql,
    massive_url,
    aggregate_session_rows,
    parse_hour_row,
    requested_tickers,
    unit_id,
)
from research.bar_gpt.v1.cohort import (
    BAR_GPT_COHORT_2TB,
    BAR_GPT_DAILY_BOOTSTRAP_MANIFEST_TABLE,
    BAR_GPT_DAILY_BOOTSTRAP_TABLE,
)
from research.bar_gpt.v1.run_build_daily_context import parse_args as parse_launcher_args


def args() -> argparse.Namespace:
    return argparse.Namespace(
        database="market_sip_compact",
        target_table=BAR_GPT_DAILY_BOOTSTRAP_TABLE,
        manifest_table=BAR_GPT_DAILY_BOOTSTRAP_MANIFEST_TABLE,
        storage_policy="live_market_ssd",
    )


class DailyContextContractTest(unittest.TestCase):
    def test_launcher_defaults_cover_pre_2019_canonical_cohort(self) -> None:
        parsed, extra = parse_launcher_args([])
        self.assertFalse(extra)
        self.assertEqual(parsed.start_date, "2016-01-01")
        self.assertEqual(parsed.end_date, "2019-01-02")
        self.assertEqual(tuple(parsed.tickers.split(",")), BAR_GPT_COHORT_2TB)
        self.assertEqual(parsed.target_table, BAR_GPT_DAILY_BOOTSTRAP_TABLE)

    def test_source_url_is_range_bounded_sorted_and_unadjusted(self) -> None:
        url = massive_url(
            "https://api.massive.com", "BRK.B", dt.date(2016, 1, 1), dt.date(2019, 1, 2)
        )
        self.assertIn("/v2/aggs/ticker/BRK.B/range/1/hour/2016-01-01/2019-01-01", url)
        self.assertIn("adjusted=false", url)
        self.assertIn("sort=asc", url)
        self.assertIn("limit=50000", url)
        self.assertNotIn("apiKey", url)

    def test_provider_row_maps_to_explicit_trade_only_context(self) -> None:
        first = parse_hour_row(
            "AAPL",
            {"t": 1_546_419_600_000, "o": 154.4, "h": 154.7, "l": 153.01, "c": 154.7, "v": 24_033, "vw": 154.233, "n": 180},
            dt.date(2019, 1, 1), dt.date(2019, 1, 3),
        )
        last = parse_hour_row(
            "AAPL",
            {"t": 1_546_473_600_000, "o": 146.1, "h": 146.47, "l": 145.92, "c": 146.0, "v": 255_074, "vw": 146.0841, "n": 1_845},
            dt.date(2019, 1, 1), dt.date(2019, 1, 3),
        )
        self.assertIsNotNone(first)
        self.assertIsNotNone(last)
        row = aggregate_session_rows(
            "AAPL", [first[1], last[1]], "2026-08-02T12:00:00.000+00:00"  # type: ignore[index]
        )
        self.assertEqual(row["session_date"], "2019-01-02")
        self.assertEqual(row["adjusted"], 0)
        self.assertEqual(row["open"], 154.4)
        self.assertEqual(row["close"], 146.0)
        self.assertEqual(row["volume"], 279_107.0)
        self.assertEqual(row["provider_hour_count"], 2)
        self.assertEqual(row["available_at_us"], row["bar_end_us"])

    def test_invalid_ohlc_is_rejected(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "OHLC containment"):
            parse_hour_row(
                "AAPL", {"t": 1_546_419_600_000, "o": 154.4, "h": 150.0, "l": 144.51, "c": 146.0, "v": 1},
                dt.date(2019, 1, 1), dt.date(2019, 1, 3),
            )

    def test_tables_are_versioned_unadjusted_and_idempotent(self) -> None:
        target = create_target_table_sql(args())
        manifest = create_manifest_table_sql(args())
        self.assertIn("adjusted UInt8", target)
        self.assertIn("ReplacingMergeTree(pulled_at)", target)
        self.assertIn("ORDER BY (ticker, session_date)", target)
        self.assertIn("storage_policy = 'live_market_ssd'", target)
        self.assertIn("ReplacingMergeTree(completed_at)", manifest)
        self.assertIn("contract_sha256 FixedString(64)", manifest)

    def test_units_bind_ticker_range_and_contract(self) -> None:
        value = unit_id("AAPL", dt.date(2016, 1, 1), dt.date(2019, 1, 2))
        self.assertIn("AAPL:2016-01-01:2019-01-02", value)
        self.assertTrue(value.endswith(CONTRACT_SHA256[:16]))
        self.assertEqual(requested_tickers("msft,AAPL,msft"), ("AAPL", "MSFT"))


class DailyContextTerminalTest(unittest.TestCase):
    def test_compact_render_keeps_durable_progress_and_provider_state(self) -> None:
        reporter = DailyBootstrapReporter(
            Path(r"D:\TradingML\runtimes\bar_gpt\v1\build_daily_context\test\build.jsonl"),
            100,
            layout="rich",
        )
        reporter.state = "running"
        reporter.current = "AAPL"
        reporter.completed = 41
        reporter.skipped = 12
        reporter.rows = 28_744
        reporter.requests = 41
        reporter.retries = 2
        reporter.last_rows = 742
        reporter.last_seconds = 1.25
        reporter.message = "Certified AAPL: 742 sessions"
        output = io.StringIO()
        console = Console(file=output, width=58, force_terminal=False, color_system=None)
        reporter._console = console
        console.print(reporter._render())
        rendered = " ".join(output.getvalue().split())
        self.assertIn("AAPL", rendered)
        self.assertIn("tickers 41/100", rendered)
        self.assertIn("rows 28,744", rendered)
        self.assertIn("requests 41", rendered)
        self.assertIn("742 rows in 1.25s", rendered)


if __name__ == "__main__":
    unittest.main()
