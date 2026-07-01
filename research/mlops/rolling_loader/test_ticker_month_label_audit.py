from __future__ import annotations

import datetime as dt
import unittest
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

from research.mlops.rolling_loader.audit_ticker_month_cache import (
    TickerMonthAuditConfig,
    _compare_source_label,
    _horizon_inside_session,
    _sample_horizon_indexes,
    _source_label_query_sql,
)


class TickerMonthLabelAuditFixtureTest(unittest.TestCase):
    def test_session_boundary_is_inclusive_at_20_00_et(self) -> None:
        origin = dt.datetime(2019, 2, 1, 19, 59, 59, 900000, tzinfo=ZoneInfo("America/New_York")).timestamp()
        origin_us = int(origin * 1_000_000)
        self.assertTrue(_horizon_inside_session(origin_us, 100_000))
        self.assertFalse(_horizon_inside_session(origin_us, 101_000))

    def test_horizon_sampling_keeps_first_middle_last(self) -> None:
        self.assertEqual(_sample_horizon_indexes(20, 3, __import__("random").Random(17)), [0, 10, 19])

    def test_compare_source_label_accepts_exact_fixture(self) -> None:
        cached = {
            "price_primary_int": np.asarray([101.125, 102.25], dtype=np.float32),
            "price_secondary_int": np.asarray([99.125, 100.25], dtype=np.float32),
            "size_primary_sum": np.asarray([1.5, 2.5], dtype=np.float32),
            "size_secondary_sum": np.asarray([0.5, 0.75], dtype=np.float32),
            "event_count": np.asarray([3, 4], dtype=np.uint64),
            "last_event_timestamp_us": np.asarray([1001, 1002], dtype=np.int64),
            "available": np.asarray([1, 1], dtype=np.uint8),
            "condition_halt_pause_flag": np.asarray([0, 1], dtype=np.uint8),
            "condition_resume_flag": np.asarray([0, 0], dtype=np.uint8),
            "condition_news_risk_flag": np.asarray([1, 0], dtype=np.uint8),
            "condition_luld_limit_state_flag": np.asarray([0, 1], dtype=np.uint8),
            "ticker_news_arrival_flag": np.asarray([1, 0], dtype=np.uint8),
            "sec_filing_arrival_flag": np.asarray([0, 1], dtype=np.uint8),
        }
        expected = {key: value[1].item() if hasattr(value[1], "item") else value[1] for key, value in cached.items()}
        issues: list[object] = []
        _compare_source_label(cached=cached, expected=expected, horizon_index=1, issues=issues, package_dir=Path("fixture"), origin_key="AAPL|1")
        self.assertEqual(issues, [])

    def test_source_label_sql_contains_final_flags_only(self) -> None:
        query = _source_label_query_sql(
            ticker="AAPL",
            origin_timestamp_us=1549022400000000,
            horizon_us=1_000_000,
            config=TickerMonthAuditConfig(cache_root=Path(".")),
        )
        for field in (
            "condition_halt_pause_flag",
            "condition_resume_flag",
            "condition_news_risk_flag",
            "condition_luld_limit_state_flag",
            "ticker_news_arrival_flag",
            "sec_filing_arrival_flag",
        ):
            self.assertIn(field, query)
        self.assertNotIn("condition_halt_pause_count", query)
        self.assertNotIn("opening_delay", query)
        self.assertIn("bitAnd(event_meta, 2)", query)
        self.assertIn("bitAnd(event_meta, 4)", query)
        self.assertIn("last_price_primary", query)
        self.assertIn("last_price_secondary", query)


if __name__ == "__main__":
    unittest.main()
