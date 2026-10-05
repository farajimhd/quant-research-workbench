from __future__ import annotations

import argparse
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from pipelines.market_sip.events.clickhouse_build_unified_events import (
    CANONICAL_ARCHIVE_COLUMNS,
    assert_archive_write_entrypoint,
    create_events_table_sql,
    validate_events_table_schema,
)
from pipelines.market_sip.flatfiles.download_massive_sip_flatfiles import DownloadJob
from pipelines.market_sip.flatfiles.download_update_events import (
    DayFiles,
    RemoteDayInventory,
    ResourceAwareClickHouseClient,
    build_auto_update_plan,
    clickhouse_price_int,
    confirm_auto_update,
    execution_clock_existing_source_days,
    execution_clock_coverage_batches,
    execution_clock_rows_match_archive,
    event_values_match,
    format_auto_update_summary,
    insert_execution_clock_day_sql,
    insert_execution_clock_coverage_day_sql,
    insert_direct_day_sql,
    parse_args,
    query_database_frontier,
    raw_event_union_sql,
    trade_raw_row_to_event,
    updater_query_settings,
    window_query_settings,
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
    def test_new_day_audit_requires_reporting_flags_but_legacy_skip_accepts_old_rows(self) -> None:
        expected={"ticker":"A","event_type":1,"event_meta":193,"sip_timestamp_us":100,
                  "price_primary_int":100,"price_secondary_int":0,"size_primary":1.0,
                  "size_secondary":0.0,"exchange_primary":1,"exchange_secondary":0,
                  "event_date":"2026-08-03",**{f"condition_token_{i}":0 for i in range(1,6)}}
        old=dict(expected,event_meta=1)
        self.assertFalse(event_values_match(expected,old))
        self.assertTrue(event_values_match(expected,old,allow_legacy_flags=True))

    def test_canonical_archive_schema_and_writer_boundary_are_explicit(self) -> None:
        args = argparse.Namespace(database="market_sip_compact", events_table="events")
        sql = create_events_table_sql(
            argparse.Namespace(
                database="market_sip_compact",
                events_table="events_2026",
                partition_mode="month",
                partition_buckets=256,
                storage_policy="sip_raw_ssd",
            )
        )

        self.assertNotIn("execution_timestamp_us", sql)
        assert_archive_write_entrypoint(args, "download_update_events")
        with self.assertRaisesRegex(RuntimeError, "only .*download_update_events.py"):
            assert_archive_write_entrypoint(args, "clickhouse_build_unified_events")
        with self.assertRaisesRegex(RuntimeError, "only .*download_update_events.py"):
            assert_archive_write_entrypoint(
                argparse.Namespace(database="market_sip_compact", events_table="events_2026"),
                "clickhouse_build_unified_events",
            )

    def test_canonical_archive_schema_rejects_any_extra_column(self) -> None:
        rows = [f"{name}\t{col_type}" for name, col_type in CANONICAL_ARCHIVE_COLUMNS.items()]
        rows.append("execution_timestamp_us\tUInt64")
        client = mock.Mock()
        client.query_tsv.return_value = "\n".join(rows)

        with self.assertRaisesRegex(RuntimeError, "unexpected=.*execution_timestamp_us"):
            validate_events_table_schema(
                client,
                argparse.Namespace(database="market_sip_compact", events_table="events_2026"),
            )

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
        self.assertEqual(event["event_meta"], 19 | 64)
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

    def test_raw_union_pushes_ticker_filter_without_expanding_archive_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            day = _day(root, "2026-08-21", cached_quote=True, cached_trade=True)
            args = argparse.Namespace(
                condition_token_reference_table="event_condition_token_reference",
                database="market_sip_compact",
                drop_trade_correction_codes="7,8,10,11",
                flatfiles_root_ch="/mnt/d/market-data",
                flatfiles_root_win=str(root),
                tickers="SUGP",
            )

            sql = raw_event_union_sql(args, day)

            self.assertEqual(sql.count("AND ticker IN ('SUGP')"), 2)
            self.assertNotIn("execution_timestamp_us", sql)
            self.assertIn("AS reporting_reason", sql)
            self.assertIn("bitOr(", sql)

    def test_execution_clock_sidecar_reconstructs_the_same_ticker_ordinal(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            day = _day(root, "2026-08-21", cached_quote=True, cached_trade=True)
            args = argparse.Namespace(
                condition_token_reference_table="event_condition_token_reference",
                continuity_table="events_ordinal_continuity",
                database="market_sip_compact",
                drop_trade_correction_codes="7,8,10,11",
                events_table="events_2026",
                execution_clock_database="q_live",
                execution_clock_table="historical_event_execution_clock_v1",
                execution_clock_coverage_table="historical_event_execution_clock_coverage_v1",
                flatfiles_root_ch="/mnt/d/market-data",
                flatfiles_root_win=str(root),
                max_memory_usage="64G",
                max_partitions_per_insert_block=1024,
                max_threads=8,
                tickers="SUGP",
            )

            sql = insert_execution_clock_day_sql(args, day, 20260821)

            self.assertIn("participant_timestamp", sql)
            self.assertIn("row_number() OVER (PARTITION BY e.ticker ORDER BY e.sip_timestamp_us, e.sequence_number", sql)
            self.assertIn("WHERE bitAnd(ordered.event_meta, 1) = 1", sql)
            self.assertIn("`q_live`.`historical_event_execution_clock_v1`", sql)

            coverage_sql = insert_execution_clock_coverage_day_sql(args, day, 20260821)
            self.assertIn("delayed_trade_report_count", coverage_sql)
            self.assertIn("intDiv(x.execution_timestamp_us, 1000000)", coverage_sql)
            self.assertIn("intDiv(e.sip_timestamp_us, 1000000)", coverage_sql)

    def test_existing_sidecar_accepts_exact_zero_trade_ticker_day(self) -> None:
        client = mock.Mock()
        client.query_tsv.side_effect = ["10\t20\t10\n", "0\t0\n"]
        args = argparse.Namespace(
            database="market_sip_compact",
            continuity_table="events_ordinal_continuity",
            events_table="events_2026",
            execution_clock_database="q_live",
            execution_clock_table="historical_event_execution_clock_v1",
            tickers="SUGP",
        )
        day = _day(Path("unused"), "2026-08-21")

        self.assertTrue(execution_clock_rows_match_archive(client, args, day))


class AutoUpdatePlanningTests(unittest.TestCase):
    def test_window_stages_disable_unspillable_parallel_scatter_without_changing_order(self) -> None:
        with mock.patch("sys.argv", ["download_update_events.py"]):
            args = parse_args()
        args.events_table = "events_2026"
        args.flatfiles_root_win = str(Path("unused").resolve())
        day = _day(Path("unused"), "2026-09-21")
        for sql in (insert_direct_day_sql(args, day, 739880), insert_execution_clock_day_sql(args, day, 739880)):
            self.assertIn("max_threads = 1", sql)
            self.assertIn("ORDER BY e.sip_timestamp_us, e.sequence_number, bitAnd(e.event_meta, 1)", sql)
        self.assertIn("max_threads = 1", window_query_settings(args))
        self.assertEqual(args.max_threads, 4)

    def test_updater_bounds_csv_parser_and_insert_buffers_independently_of_threads(self) -> None:
        with mock.patch("sys.argv", ["download_update_events.py"]):
            args = parse_args()
        settings = updater_query_settings(args)
        self.assertEqual(settings['max_memory_usage'], 16 * 1024**3)
        self.assertEqual(settings['max_threads'], 4)
        self.assertEqual(settings['input_format_parallel_parsing'], 0)
        self.assertEqual(settings['max_parsing_threads'], 1)
        self.assertEqual(settings['max_insert_block_size'], 65536)
        self.assertEqual(settings['max_bytes_before_external_sort'], 512 * 1024**2)
        self.assertEqual(settings['query_plan_join_swap_table'], 'false')

    def test_resource_admission_waits_without_cancelling_other_queries(self) -> None:
        client = ResourceAwareClickHouseClient("http://unused", "", "", server_memory_ceiling=100)
        with mock.patch("pipelines.market_sip.flatfiles.download_update_events.ClickHouseHttpClient.execute", side_effect=["101", "50", "ok"]) as execute, \
             mock.patch("pipelines.market_sip.flatfiles.download_update_events.time.sleep") as sleep, redirect_stdout(io.StringIO()):
            self.assertEqual(client.execute("INSERT INTO target SELECT 1", query_id="owned"), "ok")
        sleep.assert_called_once_with(30)
        self.assertEqual(execute.call_args.args[0], "INSERT INTO target SELECT 1")
        self.assertEqual(execute.call_args.kwargs["query_id"], "owned")
        self.assertFalse(any("KILL" in call.args[0] for call in execute.call_args_list))

    def test_resource_admission_fails_closed_without_telemetry(self) -> None:
        client = ResourceAwareClickHouseClient("http://unused", "", "", server_memory_ceiling=100)
        with mock.patch("pipelines.market_sip.flatfiles.download_update_events.ClickHouseHttpClient.execute", return_value=""), \
             self.assertRaisesRegex(RuntimeError, "telemetry is missing"):
            client.execute("INSERT INTO target SELECT 1")

    def test_coverage_batches_preserve_every_ticker_and_exact_day_ordinals(self) -> None:
        client = mock.Mock()
        client.query_tsv.return_value = "A\t10\t12\nB\t100\t104\nC\t8\t20\nD\t1\t2\n"
        args = argparse.Namespace(database="market_sip_compact", continuity_table="events_ordinal_continuity",
            tickers="", execution_clock_batch_events=6, execution_clock_batch_tickers=2)
        batches = execution_clock_coverage_batches(client, args, _day(Path("unused"), "2026-09-21"))
        self.assertEqual([batch.tickers for batch in batches], ["A,B", "C", "D"])
        self.assertEqual([bound for batch in batches for bound in batch.execution_clock_batch_bounds],
            [("A", 10, 12), ("B", 100, 104), ("C", 8, 20), ("D", 1, 2)])
        self.assertEqual(args.tickers, "")

    def test_coverage_batches_fail_closed_on_empty_duplicate_or_invalid_bounds(self) -> None:
        args = argparse.Namespace(database="market_sip_compact", continuity_table="events_ordinal_continuity", tickers="")
        for rows in ("", "A\t1\t1\n", "A\t1\t2\nA\t2\t3\n"):
            with self.subTest(rows=rows), self.assertRaises(RuntimeError):
                client = mock.Mock()
                client.query_tsv.return_value = rows
                execution_clock_coverage_batches(client, args, _day(Path("unused"), "2026-09-21"))

    def test_coverage_query_filters_archive_ordinals_and_keeps_small_join_on_right(self) -> None:
        with mock.patch("sys.argv", ["download_update_events.py"]):
            args = parse_args()
        args.events_table = "events_2026"
        day = _day(Path("unused"), "2026-09-21")
        with self.assertRaisesRegex(ValueError, "bounded ticker"):
            insert_execution_clock_coverage_day_sql(args, day, 739880)
        args.tickers = "A,B"
        args.execution_clock_batch_bounds = (("A", 10, 12), ("B", 100, 104))
        sql = insert_execution_clock_coverage_day_sql(args, day, 739880)
        self.assertIn("ticker = 'A' AND ordinal >= toUInt64(10) AND ordinal < toUInt64(12)", sql)
        self.assertLess(sql.index("FROM `market_sip_compact`.`events_2026`"), sql.index("FROM `market_sip_compact`.`events_ordinal_continuity`"))
        self.assertIn("query_plan_join_swap_table = 'false'", sql)
        self.assertIn("HAVING trade_count = clock_count", sql)

    def test_failed_frontier_requires_explicit_exact_frontier_recovery(self) -> None:
        client = mock.Mock()
        with mock.patch("sys.argv", ["download_update_events.py"]):
            base = parse_args()
        base.start_date = "2026-09-21"
        base.end_date = "2026-10-02"
        base.retry_failed = True
        base.force_day_delete = True
        with mock.patch("pipelines.market_sip.flatfiles.download_update_events.clickhouse_table_exists", return_value=True), \
             mock.patch("pipelines.market_sip.flatfiles.download_update_events.first_tsv_row", return_value=["13204", "2026-09-21"]), \
             mock.patch("pipelines.market_sip.flatfiles.download_update_events.latest_day_status", return_value="failed"):
            self.assertEqual(query_database_frontier(client, base, required=False), "2026-09-21")
            for required, start, retry, delete in ((True, "2026-09-21", True, True),
                (False, "2026-09-22", True, True), (False, "2026-09-21", False, True),
                (False, "2026-09-21", True, False)):
                args = argparse.Namespace(**vars(base))
                args.start_date, args.retry_failed, args.force_day_delete = start, retry, delete
                with self.subTest(required=required, start=start, retry=retry, delete=delete), self.assertRaisesRegex(RuntimeError, "refusing to append"):
                    query_database_frontier(client, args, required=required)

    def test_manual_selection_accepts_failed_frontier_recovery_but_rejects_internal_holes(self) -> None:
        with mock.patch("sys.argv", ["download_update_events.py"]):
            args = parse_args()
        args.start_date, args.end_date = "2026-09-21", "2026-09-22"
        args.retry_failed, args.force_day_delete = True, True
        days = [_day(Path("unused"), value) for value in ("2026-09-21", "2026-09-22")]
        inventory = RemoteDayInventory(tuple(days), ())
        with mock.patch("pipelines.market_sip.flatfiles.download_update_events.latest_day_status", return_value="failed"):
            validate_manual_append_selection(mock.Mock(), args, inventory, days, "2026-09-21")
            with self.assertRaisesRegex(RuntimeError, "internal hole"):
                validate_manual_append_selection(mock.Mock(), args, inventory, days, "2026-09-22")

    def test_execution_clock_existing_days_come_from_continuity_without_remote_discovery(self) -> None:
        client = mock.Mock()
        client.query_tsv.return_value = "2026-08-20\n2026-08-21\n"
        args = argparse.Namespace(
            database="market_sip_compact",
            continuity_table="events_ordinal_continuity",
            start_date="2026-08-20",
            end_date="2026-08-21",
            tickers="SUGP,JUNS",
        )

        days = execution_clock_existing_source_days(client, args)

        self.assertEqual([day.source_date for day in days], ["2026-08-20", "2026-08-21"])
        sql = client.query_tsv.call_args.args[0]
        self.assertIn("ticker IN ('JUNS', 'SUGP')", sql)

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

    def test_execution_clock_repair_accepts_explicit_ticker_scope(self) -> None:
        with mock.patch(
            "sys.argv",
            [
                "download_update_events.py",
                "--start-date",
                "2026-08-20",
                "--end-date",
                "2026-08-21",
                "--tickers",
                "SUGP,JUNS",
                "--execution-clock-only",
            ],
        ):
            args = parse_args()

        self.assertTrue(args.execution_clock_only)
        self.assertEqual(args.tickers, "SUGP,JUNS")


if __name__ == "__main__":
    unittest.main()
