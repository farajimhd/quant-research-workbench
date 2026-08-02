from __future__ import annotations

import argparse
import datetime as dt
import unittest

from research.bar_gpt.v1.build_adjusted_1s import (
    FEATURE_VERSION,
    adjusted_table_columns,
    bulk_month_sql,
    factor_rows,
    identity_alias_intervals,
    identity_exclusion_sql,
    scaled_feature_expression,
    split_day_sql,
)
from research.bar_gpt.v1.build_adjusted_daily_sessions import (
    CONTRACT_SHA256,
    SESSION_BOUNDS,
    _empty_or_rollup,
    _parse_window,
    create_target_table_sql as create_daily_table_sql,
    massive_url,
    parse_ticker_segments,
    reviewed_ticker_segments,
)
from research.bar_gpt.v1.run_build_adjusted_1s import resolve_adjustment_asof as resolve_1s_asof
from research.bar_gpt.v1.run_build_adjusted_daily_sessions import resolve_adjustment_asof as resolve_daily_asof


def daily_args() -> argparse.Namespace:
    return argparse.Namespace(database="market_sip_compact", target_table="daily_v2", storage_policy="live_market_ssd")


def one_second_args() -> argparse.Namespace:
    return argparse.Namespace(
        database="market_sip_compact", target_table="bars_v2", source_table="bars_v1",
        factor_table="factors_v2", events_table_base="events", storage_policy="live_market_ssd",
        max_threads=8, max_memory_usage="48G", max_bytes_before_external_group_by="12G",
    )


class AdjustedDailyContractTest(unittest.TestCase):
    def test_request_is_adjusted_30_minute_and_range_bounded(self) -> None:
        url = massive_url("https://api.massive.com", "AAPL", dt.date(2017, 1, 1), dt.date(2020, 1, 2))
        self.assertIn("/range/30/minute/2017-01-01/2020-01-01", url)
        self.assertIn("adjusted=true", url)
        self.assertIn("sort=asc", url)
        self.assertNotIn("apiKey", url)
        self.assertEqual(len(CONTRACT_SHA256), 64)

    def test_three_explicit_session_bounds_and_absence(self) -> None:
        self.assertEqual([name for name, _left, _right in SESSION_BOUNDS], ["premarket", "regular", "after_hours"])
        row = _empty_or_rollup("AAPL", "AAPL", dt.date(2020, 8, 31), "premarket", [],
                               "2026-08-02 12:00:00.000", dt.date(2026, 8, 2), "a" * 64)
        self.assertEqual(row["present"], 0)
        self.assertIsNone(row["open"])
        self.assertEqual(row["volume"], 0)
        self.assertEqual(row["available_at_us"], row["bar_end_us"])

    def test_provider_window_is_assigned_by_new_york_boundary(self) -> None:
        timestamp_ms = int(dt.datetime(2020, 8, 31, 9, 30, tzinfo=dt.timezone(dt.timedelta(hours=-4))).timestamp() * 1000)
        row = _parse_window("AAPL", {"t": timestamp_ms, "o": 127, "h": 130, "l": 126, "c": 129, "v": 10, "vw": 128, "n": 2},
                            dt.date(2020, 8, 31), dt.date(2020, 9, 1))
        self.assertIsNotNone(row)
        self.assertEqual(row["session_kind"], "regular")  # type: ignore[index]

    def test_table_binds_adjustment_snapshot_and_session_identity(self) -> None:
        ddl = create_daily_table_sql(daily_args())
        self.assertIn("adjustment_asof_date", ddl)
        self.assertIn("ORDER BY (ticker, session_date, session_kind)", ddl)
        self.assertIn("Nullable(Float64)", ddl)
        self.assertIn("provider_ticker", ddl)
        self.assertIn("split_schedule_sha256", ddl)

    def test_point_in_time_ticker_chain_preserves_canonical_identity(self) -> None:
        payload = {"results": {"events": [
            {"type": "ticker_change", "date": "2012-05-18", "ticker_change": {"ticker": "FB"}},
            {"type": "ticker_change", "date": "2022-06-09", "ticker_change": {"ticker": "META"}},
        ]}}
        self.assertEqual(
            parse_ticker_segments("META", dt.date(2017, 1, 1), dt.date(2023, 1, 1), payload),
            (("FB", dt.date(2017, 1, 1), dt.date(2022, 6, 9)),
             ("META", dt.date(2022, 6, 9), dt.date(2023, 1, 1))),
        )

    def test_mismatched_experimental_chain_falls_back_to_literal_ticker(self) -> None:
        payload = {"results": {"events": [
            {"type": "ticker_change", "date": "2015-10-06", "ticker_change": {"ticker": "GOOG"}},
        ]}}
        self.assertEqual(parse_ticker_segments("GOOGL", dt.date(2017, 1, 1), dt.date(2018, 1, 1), payload),
                         (("GOOGL", dt.date(2017, 1, 1), dt.date(2018, 1, 1)),))

    def test_reviewed_meta_chain_overrides_ticker_reuse(self) -> None:
        self.assertEqual(
            reviewed_ticker_segments("META", dt.date(2019, 1, 1), dt.date(2023, 1, 1)),
            (("FB", dt.date(2019, 1, 1), dt.date(2022, 6, 9)),
             ("META", dt.date(2022, 6, 9), dt.date(2023, 1, 1))),
        )


class AdjustedOneSecondContractTest(unittest.TestCase):
    def test_multiple_future_splits_compound_only_over_applicable_period(self) -> None:
        rows = factor_rows(
            ("XYZ",), dt.date(2020, 1, 1), dt.date(2020, 1, 5), dt.date(2026, 1, 1),
            {"XYZ": [(dt.date(2020, 1, 2), 0.5, 2.0), (dt.date(2020, 1, 4), 0.25, 4.0)]}, "a" * 64,
        )
        by_day = {row["local_date"]: row for row in rows}
        self.assertEqual(by_day["2020-01-01"]["future_price_factor"], 0.125)
        self.assertEqual(by_day["2020-01-02"]["future_price_factor"], 0.25)
        self.assertEqual(by_day["2020-01-02"]["split_day_price_factor"], 0.5)
        self.assertEqual(by_day["2020-01-04"]["future_price_factor"], 1.0)
        self.assertEqual(by_day["2020-01-04"]["split_day_price_factor"], 0.25)

    def test_sufficient_statistics_receive_dimensionally_correct_scaling(self) -> None:
        self.assertIn("future_price_factor", scaled_feature_expression("trade_open"))
        self.assertIn("future_size_factor * f.future_size_factor", scaled_feature_expression("trade_size_squared_sum"))
        price_size = scaled_feature_expression("trade_price_size_sum")
        self.assertIn("future_price_factor", price_size)
        self.assertIn("future_size_factor", price_size)
        self.assertEqual(scaled_feature_expression("queue_imbalance_sum"), "s.`queue_imbalance_sum`")
        self.assertIn("future_price_factor * f.future_price_factor", scaled_feature_expression("spread_squared_sum"))

    def test_bulk_copy_excludes_split_days_and_binds_basis(self) -> None:
        sql = bulk_month_sql(one_second_args(), dt.date(2020, 8, 15), dt.date(2020, 9, 1),
                             dt.date(2026, 8, 2), "b" * 64, ("AAPL",))
        self.assertIn("f.split_day_action_count=0", sql)
        self.assertIn("2020-08-15", sql)
        self.assertIn(FEATURE_VERSION, sql)
        self.assertIn("linear_sufficient_stats", sql)
        self.assertIn("split_schedule_sha256", " ".join(name for name, _kind in adjusted_table_columns()))
        self.assertIn("source_ticker", " ".join(name for name, _kind in adjusted_table_columns()))

    def test_split_day_replay_classifies_each_price_leg_and_scales_size_reciprocally(self) -> None:
        sql = split_day_sql(one_second_args(), dt.date(2020, 8, 31), "AAPL", dt.date(2026, 8, 2),
                            "c" * 64, 129.0, 1.0, 1.0, 0.25, 4.0)
        self.assertIn("primary_stale", sql)
        self.assertIn("secondary_stale", sql)
        self.assertIn("event_replay_split_day", sql)
        self.assertIn("raw_primary*0.25", sql)
        self.assertIn("size_primary)*4", sql)
        self.assertIn("GROUP BY local_date_value,ticker,second_bucket_index,second_start_us", sql)

    def test_identity_alias_interval_excludes_reused_canonical_ticker(self) -> None:
        intervals = identity_alias_intervals(("META",), dt.date(2019, 1, 1), dt.date(2023, 1, 1))
        self.assertEqual(intervals, [("META", "FB", dt.date(2019, 1, 1), dt.date(2022, 6, 9))])
        clause = identity_exclusion_sql(intervals)
        self.assertIn("s.ticker='META'", clause)
        self.assertIn("2022-06-09", clause)
        sql = split_day_sql(
            one_second_args(), dt.date(2022, 6, 8), "FB", dt.date(2026, 8, 2),
            "d" * 64, 1.0, 1.0, 1.0, 1.0, 1.0,
            output_ticker="META", build_method="event_replay_identity_alias",
        )
        self.assertIn("'META', second_bucket_index", sql)
        self.assertIn("upper(ticker)='FB'", sql)
        self.assertIn("event_replay_identity_alias", sql)

    def test_launchers_resolve_auto_asof_internally(self) -> None:
        expected = dt.datetime.now(dt.timezone.utc).date().isoformat()
        self.assertIn(resolve_1s_asof("auto"), {expected, (dt.date.fromisoformat(expected) - dt.timedelta(days=1)).isoformat()})
        self.assertEqual(resolve_daily_asof("2026-08-02"), "2026-08-02")


if __name__ == "__main__":
    unittest.main()
