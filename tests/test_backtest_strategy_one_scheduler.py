"""Sparse entry plus active liquidity scheduling remains causal and exact."""
import asyncio
import numpy as np
import pytest
from types import SimpleNamespace

from src.backend.backtest_strategy_one_scheduler import (
    StrategyOneBoundaryScheduler, persisted_active_market_source,
    run_strategy_one_boundaries,
)
from src.backend.backtest_strategy_one_market import (
    StrategyOneDecisionCandidate, attach_sparse_candidate_evidence,
)
from src.backend.backtest_strategy_one_preparation import (
    PreparedStrategyOneTicker, StrategyOneEntryCursor,
)
from src.backend.backtest_market_data import (
    CertifiedMarketDayPlan, ExecutionInterval, MarketDayUnit,
)
from src.backend.backtest_liquidity_price import PriceLevelPlan, PriceLevelUnit
from src.trading_runtime.ibkr_schema import OrderStatus
from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter


DAY = "2026-08-18"


def test_financially_active_symbols_include_open_orders_and_nonflat_positions():
    broker = SimulatedBrokerAdapter(["DU1", "DU2"])
    broker._orders_by_ticker = {
        "AAA": [SimpleNamespace(status=OrderStatus.SUBMITTED)],
        "BBB": [SimpleNamespace(status=OrderStatus.CANCELLED)],
        "CCC": [SimpleNamespace(status=OrderStatus.INACTIVE)],
    }
    broker._positions["DU1"][1] = SimpleNamespace(
        ticker="DDD", quantity=5.0)
    broker._positions["DU2"][2] = SimpleNamespace(
        ticker="eee", quantity=-2.0)
    broker._positions["DU2"][3] = SimpleNamespace(
        ticker="FFF", quantity=0.0)
    assert broker.financially_active_tickers() == (
        "AAA", "CCC", "DDD", "EEE")


def test_reconcile_financial_tickers_adds_after_boundary_and_removes_flat():
    requests = []

    def source(ticker, after):
        requests.append((ticker, after))
        return iter((group(ticker, 200),))

    clock = StrategyOneBoundaryScheduler(
        session_date=DAY, candidate_rows=iter((candidate("AAA", 100),)),
        active_source=source)
    assert clock.pop_next().boundary_ms == 100
    clock.reconcile_financial_tickers(("AAA",))
    assert clock.active_tickers == ("AAA",)
    assert clock.pop_next().boundary_ms == 200
    clock.reconcile_financial_tickers(())
    assert clock.active_tickers == ()
    assert clock.pop_next() is None
    assert requests == [("AAA", 100)]
    clock.close()


def test_failed_financial_reconciliation_preserves_existing_active_ticker():
    def source(ticker, after):
        if ticker == "BBB":
            raise RuntimeError("missing active liquidity")
        return iter((group(ticker, 300),))

    clock = StrategyOneBoundaryScheduler(
        session_date=DAY, candidate_rows=iter((candidate("AAA", 100),)),
        active_source=source)
    clock.pop_next()
    clock.reconcile_financial_tickers(("AAA",))
    with pytest.raises(RuntimeError, match="missing active liquidity"):
        clock.reconcile_financial_tickers(("BBB",))
    assert clock.active_tickers == ("AAA",)
    clock.close()


def test_coordinator_applies_all_broker_rows_before_decisions_and_tracks_orders():
    actions = []
    active = set()

    def source(ticker, after):
        return iter((group(ticker, boundary) for boundary in (200, 300)
                     if boundary > after))

    async def broker(ticker, rows, boundary):
        actions.append((boundary, "broker", ticker))

    async def decision(ticker, rows, candidate_row):
        boundary = rows[100]["boundary_ms"]
        actions.append((boundary, "candidate" if candidate_row else "active", ticker))
        if ticker == "AAA" and boundary == 100:
            active.add(ticker)
        if ticker == "AAA" and boundary == 200:
            active.remove(ticker)

    async def finish(work):
        actions.append((work.boundary_ms, "finish", ""))

    scheduler = StrategyOneBoundaryScheduler(
        session_date=DAY,
        candidate_rows=iter((candidate("AAA", 100), candidate("BBB", 200))),
        active_source=source)
    count = asyncio.run(run_strategy_one_boundaries(
        scheduler, process_broker_row=broker, evaluate_ticker=decision,
        financially_active_tickers=lambda: tuple(sorted(active)),
        finish_boundary=finish))
    assert count == 2
    assert actions == [
        (100, "broker", "AAA"), (100, "candidate", "AAA"), (100, "finish", ""),
        (200, "broker", "AAA"), (200, "broker", "BBB"),
        (200, "active", "AAA"), (200, "candidate", "BBB"), (200, "finish", ""),
    ]
    with pytest.raises(RuntimeError, match="closed"):
        scheduler.pop_next()


def test_candidate_only_coordinator_has_no_per_boundary_thread_handoff(monkeypatch):
    from src.backend import backtest_strategy_one_scheduler as subject

    async def unexpected_thread(*_args, **_kwargs):
        pytest.fail("in-memory candidate scheduling used a worker thread")

    monkeypatch.setattr(subject.asyncio, "to_thread", unexpected_thread)

    async def noop(*_args):
        pass

    scheduler = StrategyOneBoundaryScheduler(
        session_date=DAY,
        candidate_rows=iter((candidate("AAA", 100), candidate("BBB", 200))),
        active_source=lambda ticker, after: iter(()))
    assert asyncio.run(run_strategy_one_boundaries(
        scheduler, process_broker_row=noop, evaluate_ticker=noop,
        financially_active_tickers=lambda: (),
        finish_boundary=noop)) == 2


def shared_row(ticker, boundary):
    return {"session_date": DAY, "ticker": ticker, "boundary_ms": boundary,
            "resolution_ms": 100, "close_int": 100_000,
            "low_int": 99_000, "high_int": 101_000,
            "price_valid": 1, "extremes_valid": 1,
            "bid_int": 99_900, "ask_int": 100_100, "quote_valid": 1,
            "quote_timestamp_us": 1, "execution_vwap": 10.,
            "bid_size": 500., "ask_size": 600.,
            "execution_volume": 100.,
            "execution_price_levels": ({"price_int": 100_000, "volume": 100.},),
            "cumulative_volume": 25_000., "cumulative_notional": 250_000.,
            "indicator_resolution_ms": 100, "macd_line": .2,
            "macd_signal": .1, "previous_close": 9.}


def candidate(ticker, boundary, **overrides):
    row = {**shared_row(ticker, boundary), "trade_count": 3, **overrides}
    evidence = StrategyOneEntryCursor(
        boundary, ticker, 0, boundary, (0, 0, 0, 0), 0, 1)
    return StrategyOneDecisionCandidate(row, evidence)


def group(ticker, boundary):
    row = {**shared_row(ticker, boundary), "trade_count": 3}
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
    assert [item.market_row["ticker"] for item in first.candidate_rows] == ["AAA"]
    clock.activate("AAA")
    second = clock.pop_next()
    assert second.boundary_ms == 200
    assert [ticker for ticker, _ in second.broker_rows] == ["AAA"]
    assert second.candidate_rows == ()
    third = clock.pop_next()
    assert third.boundary_ms == 300
    assert [ticker for ticker, _ in third.broker_rows] == ["AAA", "BBB"]
    assert [item.market_row["ticker"] for item in third.candidate_rows] == ["BBB"]
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
                             candidate("AAA", 300, bid_int=101))),
        active_source=conflicting)
    clock.pop_next()
    clock.activate("AAA")
    with pytest.raises(ValueError, match="market rows disagree"):
        clock.pop_next()
    clock.close()


@pytest.mark.parametrize("field,active,candidate_value", [
    ("low_int", 99_000, 98_000),
    ("high_int", 101_000, 102_000),
    ("ask_size", 600., 400.),
    ("execution_price_levels", ({"price_int": 100_000, "volume": 100.},),
     ({"price_int": 100_100, "volume": 100.},)),
    ("cumulative_volume", 25_000, 26_000),
    ("macd_line", .2, .3),
    ("previous_close", 9., 10.),
])
def test_active_candidate_rejects_any_shared_entry_evidence_divergence(
        field, active, candidate_value):
    def source(ticker, after):
        boundary, rows = group(ticker, 300)
        rows[100][field] = active
        return iter(((boundary, rows),))

    clock = StrategyOneBoundaryScheduler(
        session_date=DAY,
        candidate_rows=iter((candidate("AAA", 100),
                             candidate("AAA", 300, **{field: candidate_value}))),
        active_source=source)
    clock.pop_next()
    clock.activate("AAA")
    with pytest.raises(ValueError, match="market rows disagree"):
        clock.pop_next()
    clock.close()


def test_active_candidate_rejects_trade_count_divergence():
    def source(ticker, after):
        boundary, rows = group(ticker, 300)
        rows[100]["trade_count"] = 3
        return iter(((boundary, rows),))

    clock = StrategyOneBoundaryScheduler(
        session_date=DAY,
        candidate_rows=iter((candidate("AAA", 100),
                             candidate("AAA", 300, trade_count=4))),
        active_source=source)
    clock.pop_next()
    clock.activate("AAA")
    with pytest.raises(ValueError, match="market rows disagree"):
        clock.pop_next()
    clock.close()


@pytest.mark.parametrize("missing_from", ["active", "candidate"])
def test_active_candidate_rejects_missing_shared_evidence(missing_from):
    def source(ticker, after):
        boundary, rows = group(ticker, 300)
        if missing_from == "active":
            del rows[100]["quote_timestamp_us"]
        return iter(((boundary, rows),))

    later = candidate("AAA", 300)
    if missing_from == "candidate":
        del later.market_row["quote_timestamp_us"]
    clock = StrategyOneBoundaryScheduler(
        session_date=DAY,
        candidate_rows=iter((candidate("AAA", 100), later)),
        active_source=source)
    clock.pop_next()
    clock.activate("AAA")
    with pytest.raises(ValueError, match="market rows disagree"):
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


def test_scheduler_requires_joined_closed_bar_evidence():
    with pytest.raises(ValueError, match="paired closed-bar evidence"):
        StrategyOneBoundaryScheduler(
            session_date=DAY, candidate_rows=iter((shared_row("AAA", 100),)),
            active_source=lambda ticker, after: iter(()))

    item = PreparedStrategyOneTicker(
        "AAA", 1_000, np.array([42]), np.array([31_000]),
        np.array([30_000]), np.array([[31_000, 30_000, 30_000, 30_000]]),
        np.array([30_000]), np.array([99_000]))
    row = candidate("AAA", 31_000).market_row
    joined = attach_sparse_candidate_evidence((row,), (item,))
    clock = StrategyOneBoundaryScheduler(
        session_date=DAY, candidate_rows=iter(joined),
        active_source=lambda ticker, after: iter(()))
    work = clock.pop_next()
    assert work.broker_rows[0][1][100] is row
    assert work.candidate_rows[0].evidence.stop_low_int == 99_000
    assert work.candidate_rows[0].evidence.macd_boundary_ms == (
        31_000, 30_000, 30_000, 30_000)
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
                row = dict(group("AAA", 200)[1][100])
                row["execution_price_levels"] = [[100_000, 100.]]
                return iter((row,))
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
