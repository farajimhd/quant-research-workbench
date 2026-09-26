"""Sparse entry plus active liquidity scheduling remains causal and exact."""
import pytest

from src.backend.backtest_strategy_one_scheduler import StrategyOneBoundaryScheduler


DAY = "2026-08-18"


def candidate(ticker, boundary):
    return {"session_date": DAY, "ticker": ticker, "boundary_ms": boundary}


def group(ticker, boundary):
    row = {"session_date": DAY, "ticker": ticker,
           "boundary_ms": boundary, "resolution_ms": 100}
    return boundary, {100: row}


def test_candidates_merge_with_active_broker_rows_in_causal_order():
    requests = []

    def source(ticker, after):
        requests.append((ticker, after))
        return iter((group(ticker, boundary) for boundary in (200, 300, 500)
                     if boundary > after))

    clock = StrategyOneBoundaryScheduler(
        session_date=DAY,
        candidate_rows=iter((candidate("AAA", 100), candidate("BBB", 300))),
        active_source=source)
    first = clock.pop_next()
    assert (first.boundary_ms, first.broker_rows,
            [row["ticker"] for row in first.candidate_rows]) == (100, (), ["AAA"])
    clock.activate("AAA")
    second = clock.pop_next()
    assert second.boundary_ms == 200
    assert [ticker for ticker, _ in second.broker_rows] == ["AAA"]
    assert second.candidate_rows == ()
    third = clock.pop_next()
    assert third.boundary_ms == 300
    assert [ticker for ticker, _ in third.broker_rows] == ["AAA"]
    assert [row["ticker"] for row in third.candidate_rows] == ["BBB"]
    clock.deactivate("AAA")
    assert clock.pop_next() is None
    assert requests == [("AAA", 100)]
    clock.close()


def test_reactivation_cannot_replay_stale_prefetched_liquidity():
    calls = 0

    def source(ticker, after):
        nonlocal calls
        calls += 1
        return iter((group(ticker, 300 if calls == 1 else 400),))

    clock = StrategyOneBoundaryScheduler(
        session_date=DAY,
        candidate_rows=iter((candidate("AAA", 100), candidate("BBB", 200))),
        active_source=source)
    assert clock.pop_next().boundary_ms == 100
    clock.activate("AAA")
    assert clock.pop_next().boundary_ms == 200
    clock.deactivate("AAA")
    clock.activate("AAA")
    resumed = clock.pop_next()
    assert resumed.boundary_ms == 400
    assert [ticker for ticker, _ in resumed.broker_rows] == ["AAA"]
    assert clock.exhausted_tickers == ("AAA",)
    assert clock.pop_next() is None
    clock.deactivate("AAA")
    clock.close()


def test_source_exhaustion_does_not_implicitly_close_financial_state():
    clock = StrategyOneBoundaryScheduler(
        session_date=DAY, candidate_rows=iter((candidate("AAA", 100),)),
        active_source=lambda ticker, after: iter(()))
    clock.pop_next()
    clock.activate("AAA")
    assert clock.exhausted_tickers == ("AAA",)
    assert clock.pop_next() is None
    clock.deactivate("AAA")
    assert clock.exhausted_tickers == ()
    clock.close()


def test_invalid_candidate_or_active_source_fails_closed():
    duplicate = StrategyOneBoundaryScheduler(
        session_date=DAY,
        candidate_rows=iter((candidate("AAA", 100), candidate("AAA", 100))),
        active_source=lambda ticker, after: iter(()))
    with pytest.raises(ValueError, match="unique causal"):
        duplicate.pop_next()
    clock = StrategyOneBoundaryScheduler(
        session_date=DAY, candidate_rows=iter((candidate("AAA", 100),)),
        active_source=lambda ticker, after: iter((group(ticker, after),)))
    clock.pop_next()
    with pytest.raises(ValueError, match="completed ticker boundary"):
        clock.activate("AAA")
    clock.close()
