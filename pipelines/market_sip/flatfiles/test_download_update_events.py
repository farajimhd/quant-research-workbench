from __future__ import annotations

import argparse
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from pipelines.market_sip.flatfiles.download_massive_sip_flatfiles import DownloadJob
from pipelines.market_sip.flatfiles.download_update_events import (
    DayFiles,
    RemoteDayInventory,
    build_auto_update_plan,
    clickhouse_price_int,
    confirm_auto_update,
    format_auto_update_summary,
    parse_args,
    trade_raw_row_to_event,
    validate_manual_append_selection,
)


def _day(root: Path, source_date: str, *, cached_quote: bool = False, cached_trade: bool = False) -> DayFiles:
    quote_path = root / f"quotes-{source_date}.csv.gz"
    trade_path = root / f"trades-{source_date}.csv.gz"
    if cached_quote:
        quote_path.write_bytes(b"q" * 10)
    if cached_trade:
        trade_path.write_bytes(b"t" * 20)
    return DayFiles(
        source_date=source_date,
        quote_job=DownloadJob("quotes", source_date, f"quotes/{source_date}.csv.gz", str(quote_path), 10),
        trade_job=DownloadJob("trades", source_date, f"trades/{source_date}.csv.gz", str(trade_path), 20),
    )


class EventEncodingTests(unittest.TestCase):
    def test_clickhouse_price_int_uses_clickhouse_half_even_rounding(self) -> None:
        self.assertEqual(clickhouse_price_int("0.76905"), 7690)

    def test_trade_raw_row_to_event_matches_omex_half_tick_insert(self) -> None:
        row = {
            "ticker": "OMEX",
            "conditions": "37",
            "correction": "0",
            "exchange": "4",
            "participant_timestamp": "1745522406849832396",
            "price": "0.76905",
            "sequence_number": "6898024",
            "sip_timestamp": "1745522406850095260",
            "size": "3",
            "tape": "3",
        }
        token_maps = {"trade_conditions": {0: 60, 37: 96}}

        event = trade_raw_row_to_event(row, token_maps)

        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event["ticker"], "OMEX")
        self.assertEqual(event["event_type"], 1)
        self.assertEqual(event["event_meta"], 19)
        self.assertEqual(event["sip_timestamp_us"], 1745522406850095)
        self.assertEqual(event["sequence_number"], 6898024)
        self.assertEqual(event["price_primary_int"], 7690)
        self.assertEqual(event["price_secondary_int"], 0)
        self.assertEqual(event["size_primary"], 3.0)
        self.assertEqual(event["size_secondary"], 0.0)
        self.assertEqual(event["exchange_primary"], 4)
        self.assertEqual(event["exchange_secondary"], 0)
        self.assertEqual([event[f"condition_token_{idx}"] for idx in range(1, 6)], [96, 60, 60, 60, 60])
        self.assertEqual(event["event_date"], "2025-04-24")


class AutoUpdatePlanningTests(unittest.TestCase):
    def test_plan_keeps_cached_files_and_counts_only_missing_download_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = _day(root, "2026-06-11", cached_quote=True, cached_trade=True)
            second = _day(root, "2026-06-12", cached_quote=True, cached_trade=False)
            inventory = RemoteDayInventory((first, second), ())

            plan = build_auto_update_plan(inventory, "2026-06-10")

            self.assertEqual([day.source_date for day in plan.days], ["2026-06-11", "2026-06-12"])
            self.assertEqual(plan.cached_files, 3)
            self.assertEqual(plan.download_files, 1)
            self.assertEqual(plan.download_bytes, 20)
            summary = format_auto_update_summary(plan)
            self.assertIn("2026-06-11 -> 2026-06-12", summary)
            self.assertIn("Complete on disk:      3 / 4 files", summary)

    def test_plan_rejects_incomplete_pair_before_a_later_complete_day(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            complete = _day(Path(temp_dir), "2026-06-12")
            inventory = RemoteDayInventory((complete,), (("2026-06-11", ("trades",)),))

            with self.assertRaisesRegex(RuntimeError, "Refusing to jump over"):
                build_auto_update_plan(inventory, "2026-06-10")

    def test_manual_selection_rejects_skipping_the_next_remote_day(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            june_11 = _day(root, "2026-06-11")
            june_12 = _day(root, "2026-06-12")
            inventory = RemoteDayInventory((june_11, june_12), ())
            args = argparse.Namespace(test_mode=False)

            with self.assertRaisesRegex(RuntimeError, "Expected next complete remote source days"):
                validate_manual_append_selection(object(), args, inventory, [june_12], "2026-06-10")

    def test_noninteractive_auto_update_refuses_to_start(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            plan = build_auto_update_plan(
                RemoteDayInventory((_day(Path(temp_dir), "2026-06-11"),), ()),
                "2026-06-10",
            )
            output = io.StringIO()
            with mock.patch("sys.stdin", io.StringIO("yes\n")), redirect_stdout(output):
                approved = confirm_auto_update(plan)

            self.assertFalse(approved)
            self.assertIn("Interactive approval is required", output.getvalue())

    def test_interactive_yes_approves_the_proposed_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            plan = build_auto_update_plan(
                RemoteDayInventory((_day(Path(temp_dir), "2026-06-11"),), ()),
                "2026-06-10",
            )
            interactive_stdin = mock.Mock()
            interactive_stdin.isatty.return_value = True
            with (
                mock.patch("sys.stdin", interactive_stdin),
                mock.patch("builtins.input", return_value="yes"),
                redirect_stdout(io.StringIO()),
            ):
                self.assertTrue(confirm_auto_update(plan))

    def test_bare_cli_leaves_dates_unset_for_auto_mode(self) -> None:
        with mock.patch("sys.argv", ["download_update_events.py"]):
            args = parse_args()

        self.assertIsNone(args.start_date)
        self.assertIsNone(args.end_date)


if __name__ == "__main__":
    unittest.main()
