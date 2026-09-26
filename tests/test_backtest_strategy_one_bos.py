"""Certified BOS consumes pinned seconds without candidate-time lookahead."""

from src.backend.backtest_market_data import market_day_boundary
from src.backend.backtest_strategy_one_bos import StrategyOneBosCursor
from src.backend.backtest_strategy_one_pivot_store import (
    CertifiedPivotCoverage, CertifiedPivotPlan,
)
from src.trading_runtime.strategy_one_pivot_product import PivotInterval


def cursor() -> StrategyOneBosCursor:
    origin = round(market_day_boundary("2026-08-18", 0).timestamp() * 1_000_000)
    pivot = PivotInterval("high", 101_000, origin + 500_000,
                          origin + 1_000_000, 1_000, None)
    plan = CertifiedPivotPlan(
        "build", "2026-08-18",
        (CertifiedPivotCoverage("TEST", "attempt", "bars", 1, "hash"),),
        (("TEST", (pivot,)),), "token")
    return StrategyOneBosCursor(plan)


def bar(close: int, *, valid: int = 1):
    return {"ticker": "TEST", "resolution_ms": 1_000,
            "price_valid": valid, "close_int": close}


def test_completed_second_break_persists_to_later_sparse_candidate():
    state = cursor()
    state.observe_second("TEST", bar(100_000), 2_000)
    assert state.snapshot("TEST", boundary_ms=2_100).open_break is None
    state.observe_second("TEST", bar(102_000), 3_000)
    snapshot = state.snapshot("TEST", boundary_ms=3_500)
    assert snapshot.open_break is not None
    assert snapshot.open_break.boundary_ms == 3_000
    assert snapshot.open_break.broken_pivot.price_int == 101_000
    assert len(snapshot.visible_pivots) == 1


def test_invalid_second_prevents_fabricated_crossing_and_future_snapshot():
    state = cursor()
    state.observe_second("TEST", bar(100_000), 2_000)
    state.observe_second("TEST", bar(0, valid=0), 3_000)
    state.observe_second("TEST", bar(102_000), 4_000)
    assert state.snapshot("TEST", boundary_ms=4_000).open_break is None
    try:
        state.snapshot("TEST", boundary_ms=3_900)
    except ValueError as exc:
        assert "not causal" in str(exc)
    else:
        raise AssertionError("BOS cursor accepted a backward candidate clock")
