"""Whole-bar hindsight windows never reuse ambiguous fill buckets or RTH."""
from datetime import date
from types import SimpleNamespace

import pytest

from scripts.clickhouse.analyze_strategy_trade_excursions import bucket_window, local_us, aggregate_sql
from src.backend.backtest_market_data import assert_select_only


def test_100ms_held_excludes_both_fill_buckets():
    assert bucket_window(1_200_000, 1_500_000, 10_000_000, 100, "held") == (12, 14)
    assert bucket_window(1_200_000, 1_300_000, 10_000_000, 100, "held") == (12, 12)


def test_1s_held_excludes_coarser_bar_containing_entry_and_exit():
    assert bucket_window(12_700_000, 20_400_000, 30_000_000, 1000, "held") == (13, 20)
    assert bucket_window(12_000_000, 20_000_000, 30_000_000, 1000, "held") == (12, 19)


def test_post_exit_never_uses_forming_exit_bucket_or_crosses_requested_end():
    assert bucket_window(1_000_000, 12_700_000, 14_000_000, 1000, "post_5m") == (13, 14)
    assert bucket_window(1_000_000, 14_000_000, 14_000_000, 100, "post_15m") == (140, 140)
    pm_end = (9 * 60 + 30) * 60_000_000
    lower, upper = bucket_window(pm_end - 120_000_000, pm_end - 30_000_000, pm_end, 100, "post_15m")
    assert upper * 100_000 == pm_end and upper - lower == 300


def test_eastern_time_and_invalid_boundaries_fail_closed():
    assert local_us("2026-08-19T20:00:00.200000+00:00", date(2026, 8, 19)) == 57_600_200_000
    with pytest.raises(ValueError):
        local_us("2026-08-19T20:00:00", date(2026, 8, 19))
    with pytest.raises(ValueError):
        bucket_window(2, 1, 3, 100, "held")


def test_vectorized_query_pins_attempt_and_exact_half_open_windows():
    query = aggregate_sql(SimpleNamespace(build_id="build-one"), date(2026, 8, 19),
        [dict(episode_id="episode", ticker="AAA", attempt_id="00000000-0000-0000-0000-000000000001",
              resolution_ms=100, horizon="held", lower=144002, upper=144004)])
    assert assert_select_only(query) == query
    assert "build_id='build-one'" in query and "(ticker,attempt_id) IN" in query
    assert "b.bucket_index>=r.lower AND b.bucket_index<r.upper" in query
    assert "price_valid=1 AND extremes_valid=1" in query
    assert "uniqExact(b.bucket_index)" in query


def test_failed_run_is_rejected_before_market_read(monkeypatch):
    from scripts.clickhouse import analyze_strategy_trade_excursions as module
    monkeypatch.setattr(module, "load_v4_terminal_review_page", lambda *_a, **_kw: {"status": "failed"})
    monkeypatch.setattr(module, "certified_saved_run_plan", lambda *_a, **_kw: pytest.fail("failed run market read"))
    with pytest.raises(ValueError, match="failed runs are excluded"):
        module.build(object(), object(), "run")
