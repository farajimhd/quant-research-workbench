from __future__ import annotations

from datetime import date, datetime
import json
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from src.backend.arte_chart_reader import (
    chart_page, certified_chart_plan, eligible,
)
from src.backend.backtest_market_data import (
    CertifiedMarketDayPlan, ExecutionInterval, MarketDayUnit,
)


DAY = date(2026, 8, 18)
NY = ZoneInfo("America/New_York")
BAR_ATTEMPT = "00000000-0000-0000-0000-000000000001"
TECH_ATTEMPT = "00000000-0000-0000-0000-000000000002"


class _Client:
    def __init__(self, rows):
        self.rows = rows
        self.sql = ""

    def execute(self, sql):
        self.sql = sql
        return "\n".join(json.dumps(row) for row in self.rows)

    def close(self):
        pass


def _plan():
    units = tuple(MarketDayUnit("build", DAY.isoformat(), "SUGP", stage, attempt,
                                "source", 2, "hash") for stage, attempt in (
        ("bars", BAR_ATTEMPT), ("technical", TECH_ATTEMPT),
        ("broker_100ms", BAR_ATTEMPT),
    ))
    return CertifiedMarketDayPlan(ExecutionInterval.fixed(1_000), "build", "definition",
                                  (DAY.isoformat(),), ("SUGP",), units, (1_000,), "token")


class ArteChartReaderTests(unittest.TestCase):
    def test_chart_plan_uses_only_fenced_typed_arte_rows(self):
        from src.backend import arte_chart_reader
        arte_chart_reader._plan_cache.clear()
        client = _Client([
            {"build_id": "build", "definition_hash": "a" * 64,
             "stage": stage, "attempt_id": attempt, "source_hash": "source",
             "output_rows": 2, "output_hash": "hash"}
            for stage, attempt in (("bars", BAR_ATTEMPT),
                                   ("technical", TECH_ATTEMPT),
                                   ("broker_100ms", BAR_ATTEMPT))
        ])
        with patch("src.backend.arte_chart_reader._reader", return_value=client):
            plan = certified_chart_plan(DAY, "SUGP", "1s")
        self.assertEqual(len(plan.units), 3)
        self.assertEqual(plan.build_id, "build")
        self.assertIn("arte.market_day_build_fence_v1", client.sql)
        self.assertIn("arte.market_day_stage_certificate_v1", client.sql)
        self.assertNotIn("sqlite", client.sql.lower())
        arte_chart_reader._plan_cache.clear()

    def test_chart_plan_rejects_partial_or_ambiguous_fenced_builds(self):
        from src.backend import arte_chart_reader
        base = {"build_id": "build", "definition_hash": "a" * 64,
                "stage": "bars", "attempt_id": BAR_ATTEMPT,
                "source_hash": "source", "output_rows": 2, "output_hash": "hash"}
        for rows, reason in (([base], "incomplete"),
                             ([base, {**base, "build_id": "other"}], "ambiguous")):
            arte_chart_reader._plan_cache.clear()
            with patch("src.backend.arte_chart_reader._reader",
                       return_value=_Client(rows)):
                with self.assertRaisesRegex(RuntimeError, reason):
                    certified_chart_plan(DAY, "SUGP", "1s")

    def test_only_compatible_columns_and_auxiliary_modes_use_arte(self):
        args = dict(timeframe="1s", stage="full", indicator_columns=["bar_start", "ema_9"],
                    include_market_signals=False, include_structure=False,
                    allow_persisted_bars=True, mode="backtest")
        self.assertTrue(eligible(**args))
        self.assertFalse(eligible(**{**args, "indicator_columns": ["vwap"]}))
        self.assertFalse(eligible(**{**args, "include_structure": True}))
        self.assertFalse(eligible(**{**args, "mode": "live"}))

    def test_chart_page_uses_0400_bucket_clock_and_pinned_attempts(self):
        client = _Client([{
            "bucket_index": 14700, "open_int": 10000, "high_int": 11000,
            "low_int": 9000, "close_int": 10500, "volume": 12,
            "trade_count": 2, "notional": 12.5, "ema_9": 1.04,
            "indicator_attempt_id": TECH_ATTEMPT,
        }])
        with (patch("src.backend.arte_chart_reader.certified_chart_plan", return_value=_plan()),
              patch("src.backend.arte_chart_reader._reader", return_value=client)):
            payload = chart_page(
                session=DAY, ticker="SUGP", timeframe="1s",
                page_start=datetime(2026, 8, 18, 4, 5, tzinfo=NY),
                page_end=datetime(2026, 8, 18, 4, 6, tzinfo=NY),
                row_limit=10, stage="full", indicator_columns=["bar_start", "ema_9"],
                include_market_signals=False, include_structure=False,
                allow_persisted_bars=True, mode="backtest",
            )
        self.assertEqual(payload["bars"][0]["bar_start"], "2026-08-18T04:05:00-04:00")
        self.assertEqual(payload["indicators"][0]["ema_9"], 1.04)
        self.assertIn(BAR_ATTEMPT, client.sql)
        self.assertIn(TECH_ATTEMPT, client.sql)
        self.assertIn("bucket_index*1000>=14700000", client.sql)
        self.assertIn("(b.bucket_index+1)*1000<=14760000", client.sql)
        self.assertTrue(client.sql.startswith("SELECT "))

    def test_missing_pinned_indicator_is_not_shown_as_zero(self):
        client = _Client([{
            "bucket_index": 14700, "open_int": 10000, "high_int": 11000,
            "low_int": 9000, "close_int": 10500, "volume": 12,
            "trade_count": 2, "notional": 12.5, "ema_9": 0,
            "indicator_attempt_id": "00000000-0000-0000-0000-000000000000",
        }])
        with (patch("src.backend.arte_chart_reader.certified_chart_plan", return_value=_plan()),
              patch("src.backend.arte_chart_reader._reader", return_value=client)):
            with self.assertRaisesRegex(ValueError, "missing pinned indicator"):
                chart_page(
                    session=DAY, ticker="SUGP", timeframe="1s",
                    page_start=datetime(2026, 8, 18, 4, 5, tzinfo=NY),
                    page_end=datetime(2026, 8, 18, 4, 6, tzinfo=NY),
                    row_limit=10, stage="full", indicator_columns=["ema_9"],
                    include_market_signals=False, include_structure=False,
                    allow_persisted_bars=True, mode="backtest",
                )

    def test_bars_stage_marks_unavailable_indicator_without_selecting_it(self):
        client = _Client([{
            "bucket_index": 14700, "open_int": 10000, "high_int": 11000,
            "low_int": 9000, "close_int": 10500, "volume": 12,
            "trade_count": 2, "notional": 12.5,
        }])
        with (patch("src.backend.arte_chart_reader.certified_chart_plan", return_value=_plan()),
              patch("src.backend.arte_chart_reader._reader", return_value=client)):
            payload = chart_page(
                session=DAY, ticker="SUGP", timeframe="1s",
                page_start=datetime(2026, 8, 18, 4, 5, tzinfo=NY),
                page_end=datetime(2026, 8, 18, 4, 6, tzinfo=NY),
                row_limit=10, stage="bars", indicator_columns=["bar_start", "vwap"],
                include_market_signals=False, include_structure=False,
                allow_persisted_bars=True, mode="backtest",
            )
        self.assertEqual(len(payload["bars"]), 1)
        self.assertEqual(payload["indicator_provenance"]["unavailable_columns"], ["vwap"])
        self.assertNotIn("i.vwap", client.sql)

    def test_historical_canvas_uses_persisted_page_without_qmd_rebuild(self):
        from src.backend.trading_runtime_service import historical_bar_history_before

        page = {"bars": [{"bar_start": "2026-08-18T04:05:00-04:00", "close": 1.05}],
                "indicators": [{"bar_start": "2026-08-18T04:05:00-04:00", "ema_9": 1.04}],
                "has_more": False, "next_before": "", "source": "arte.market-day-core-v5",
                "indicator_provenance": {"authority": "arte.indicators_v1", "token": "token"}}
        with (patch("src.backend.arte_chart_reader.chart_page", return_value=page) as persisted,
              patch("src.backend.trading_runtime_service.qmd_product_request") as gateway):
            result = historical_bar_history_before(
                before=DAY, session_date=DAY, ticker="SUGP", timeframe="1s",
                as_of="2026-08-18T04:06:00-04:00", row_limit=60,
                indicator_columns=["bar_start", "ema_9"],
                include_market_signals=False, include_structure=False,
                mode="backtest", stage="full",
            )
        self.assertEqual(result["source"], "arte.market-day-core-v5")
        self.assertEqual(result["indicators"][0]["ema_9"], 1.04)
        persisted.assert_called_once()
        gateway.assert_not_called()

    def test_canvas_cache_key_changes_when_certified_build_changes(self):
        from src.backend import app

        request = dict(symbol="SUGP", timeframe="1s", session_date=DAY.isoformat(),
                       as_of="2026-08-18T04:06:00-04:00", row_limit=60,
                       indicator_columns="bar_start,ema_9", include_market_signals=False,
                       include_structure=False, mode="backtest", stage="full")
        with (patch("src.backend.arte_chart_reader.chart_revision",
                    side_effect=["build-one", "build-two"]),
              patch.object(app, "_canvas_live_chart_history",
                           side_effect=[{"source": "build-one"}, {"source": "build-two"}]) as load):
            first = app.trading_canvas_live_chart_history(**request)
            second = app.trading_canvas_live_chart_history(**request)
        self.assertEqual(first["source"], "build-one")
        self.assertEqual(second["source"], "build-two")
        self.assertEqual(load.call_count, 2)


if __name__ == "__main__":
    unittest.main()
