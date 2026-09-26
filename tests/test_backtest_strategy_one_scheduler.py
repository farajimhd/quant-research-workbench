"""Sparse entry plus active liquidity scheduling remains causal and exact."""
import pytest

from src.backend.backtest_strategy_one_scheduler import (
    StrategyOneBoundaryScheduler, persisted_active_market_source,
)
from src.backend.backtest_market_data import (
    CertifiedMarketDayPlan, ExecutionInterval, MarketDayUnit,
)
from src.backend.backtest_liquidity_price import PriceLevelPlan, PriceLevelUnit


DAY = "2026-08-18"


def candidate(ticker, boundary):
    return {"session_date": DAY, "ticker": ticker, "boundary_ms": boundary,
            "resolution_ms": 100, "price_valid": 1,
            "indicator_resolution_ms": 100}


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
    assert first.boundary_ms == 100
    assert [ticker for ticker, _ in first.broker_rows] == ["AAA"]
    assert [row["ticker"] for row in first.candidate_rows] == ["AAA"]
    clock.activate("AAA")
    second = clock.pop_next()
    assert second.boundary_ms == 200
    assert [ticker for ticker, _ in second.broker_rows] == ["AAA"]
    assert second.candidate_rows == ()
    third = clock.pop_next()
    assert third.boundary_ms == 300
    assert [ticker for ticker, _ in third.broker_rows] == ["AAA", "BBB"]
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
    closed = []

    class EmptySource:
        def __iter__(self):
            return self

        def __next__(self):
            raise StopIteration

        def close(self):
            closed.append("AAA")

    def source(ticker, after):
        return EmptySource()

    clock = StrategyOneBoundaryScheduler(
        session_date=DAY, candidate_rows=iter((candidate("AAA", 100),)),
        active_source=source)
    clock.pop_next()
    clock.activate("AAA")
    assert clock.exhausted_tickers == ("AAA",)
    assert closed == ["AAA"]
    assert clock.pop_next() is None
    clock.deactivate("AAA")
    assert clock.exhausted_tickers == ()
    clock.close()


def test_active_candidate_uses_one_broker_row_and_rejects_conflicting_quote():
    def matching(ticker, after):
        return iter((group(ticker, 300),))
    clock = StrategyOneBoundaryScheduler(
        session_date=DAY,
        candidate_rows=iter((candidate("AAA", 100), candidate("AAA", 300))),
        active_source=matching)
    clock.pop_next()
    clock.activate("AAA")
    work = clock.pop_next()
    assert work.boundary_ms == 300
    assert len(work.broker_rows) == 1
    assert len(work.candidate_rows) == 1
    clock.close()

    def conflicting(ticker, after):
        boundary, rows = group(ticker, 300)
        rows[100]["bid_int"] = 100
        return iter(((boundary, rows),))
    clock = StrategyOneBoundaryScheduler(
        session_date=DAY,
        candidate_rows=iter((candidate("AAA", 100),
                             {**candidate("AAA", 300), "bid_int": 101})),
        active_source=conflicting)
    clock.pop_next()
    clock.activate("AAA")
    with pytest.raises(ValueError, match="liquidity disagree"):
        clock.pop_next()
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


def test_active_source_reads_persisted_window_and_closes_on_deactivation():
    build = "a" * 64
    attempt = "00000000-0000-0000-0000-000000000001"
    units = tuple(MarketDayUnit(build, DAY, "AAA", stage, attempt,
                                "b" * 64, 1, "c" * 64)
                  for stage in ("bars", "technical", "broker_100ms"))
    plan = CertifiedMarketDayPlan(
        ExecutionInterval.parse("100ms"), build, "d" * 64,
        (DAY,), ("AAA",), units, (100, 1_000), "e" * 64)
    prices = PriceLevelPlan(build, (PriceLevelUnit(
        DAY, "AAA", attempt, attempt, 0, 0, 0., "f" * 64),), "g" * 64)

    class Reader:
        def __init__(self):
            self.queries = []
            self.closed = False

        def iter_json_each_row(self, sql):
            self.queries.append(sql)
            if "SELECT l.session_date" in sql:
                return iter((dict(group("AAA", 200)[1][100]),))
            return iter(({"session_date": DAY, "ticker": "AAA",
                          "boundary_ms": 1_000, "resolution_ms": 1_000},))

        def close(self):
            self.closed = True

    opened = []
    def client():
        reader = Reader()
        opened.append(reader)
        return reader

    source = persisted_active_market_source(
        plan, price_plan=prices, through_boundary_ms=1_000,
        client_factory=client)
    clock = StrategyOneBoundaryScheduler(
        session_date=DAY, candidate_rows=iter((candidate("AAA", 100),)),
        active_source=source)
    clock.pop_next()
    clock.activate("AAA")
    assert clock.pop_next().boundary_ms == 200
    assert len(opened[0].queries) == 2
    assert all("bucket_index>=144001" in sql for sql in opened[0].queries)
    clock.deactivate("AAA")
    assert opened[0].closed
    assert clock.pop_next() is None
    clock.close()
