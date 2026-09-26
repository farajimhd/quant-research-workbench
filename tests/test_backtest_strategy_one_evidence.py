"""Strategy 1 activation and BOS share one pinned V7 second-read lane."""

import asyncio
import json
import re
from datetime import date, datetime

from src.backend.backtest_market_data import (
    CertifiedMarketDayPlan, ExecutionInterval, MarketDayUnit,
    market_day_boundary,
)
from src.backend.backtest_strategy_one_activation import StrategyOneActivation
from src.backend.backtest_strategy_one_evidence import StrategyOneCausalEvidence
from src.backend.backtest_strategy_one_market import StrategyOneDecisionCandidate
from src.backend.backtest_strategy_one_scheduler import StrategyOneBoundaryWork
from src.backend.backtest_strategy_one_hod_store import CertifiedHodPlan
from src.backend.backtest_strategy_one_pivot_store import (
    CertifiedPivotCoverage, CertifiedPivotPlan,
)
from src.backend.backtest_strategy_one_preparation import StrategyOneEntryCursor
from src.backend.structural_v7_seed import CertifiedSeedPlan
from src.trading_runtime.strategy_one_pivot_product import PivotInterval
from src.trading_runtime.strategy_one_hod_product import HodContext


def test_activation_and_later_candidate_use_same_completed_second_stream():
    session = date(2026, 8, 18)
    origin_us = round(market_day_boundary(session, 0).timestamp() * 1_000_000)
    bars = [dict(ticker="TEST", resolution_ms=1000, bucket_index=14700 + index,
                 price_valid=1, extremes_valid=1, open_int=100_000,
                 high_int=102_100, low_int=99_900,
                 close_int=close, volume=100)
            for index, close in enumerate((100_000, 102_000))]
    coverage = dict(ticker="TEST", session_date="2026-08-17",
                    available_at="2026-08-18 00:00:00.000000000", state="empty",
                    level_count=0, observation_count=0, input_policy="",
                    source_extraction_version="", band_config_hash="0" * 64,
                    source_checkpoint_hash="", source_plan_hash="b" * 64)

    class Client:
        def __init__(self):
            self.bar_reads = 0

        def execute(self, sql):
            if "structural_level_coverage_v7" in sql:
                rows = [coverage]
            elif ("structural_levels_v7" in sql
                  or "structural_level_observations_v7" in sql
                  or "market_stock_split_v1" in sql):
                rows = []
            elif "arte.bars_v1" in sql:
                self.bar_reads += 1
                limits = re.search(r"bucket_index>=(\d+).*bucket_index<(\d+)", sql)
                assert limits is not None
                lower, upper = map(int, limits.groups())
                rows = [row for row in bars
                        if lower <= row["bucket_index"] < upper]
            else:
                raise AssertionError(sql)
            return "\n".join(json.dumps(row) for row in rows)

        def iter_json_each_row(self, sql):
            for line in self.execute(sql).splitlines():
                yield json.loads(line)

    market = CertifiedMarketDayPlan(
        ExecutionInterval.fixed(100), "market", "definition",
        (session.isoformat(),), ("TEST",),
        (MarketDayUnit("market", session.isoformat(), "TEST", "bars",
                       "00000000-0000-0000-0000-000000000001", "source", 2, "hash"),),
        (100, 1000), "market-token")
    seeds = CertifiedSeedPlan(
        "market", "b" * 64,
        ({**coverage, "backtest_session": session.isoformat()},),
        "v7-token", True)
    pivot = PivotInterval("high", 101_000, origin_us + 299_000_000,
                          origin_us + 300_000_000, 300_000, None)
    pivots = CertifiedPivotPlan(
        "market", session.isoformat(),
        (CertifiedPivotCoverage("TEST", "attempt", "bars", 1, "hash"),),
        (("TEST", (pivot,)),), "pivot-token")
    client = Client()
    evidence = StrategyOneCausalEvidence(
        market_plan=market, seed_plan=seeds, pivot_plan=pivots,
        hod_plan=CertifiedHodPlan("market", session.isoformat(),
            (("TEST", (HodContext(302_000, 100_000, 102_000, False, ""),)),),
            "hod-token"),
        session=session, client=client)

    async def run():
        frozen = await evidence.observe_activation(
            StrategyOneActivation(301_000, "TEST", 100_000))
        assert frozen.boundary_ms == 301_000
        assert frozen.average_gap is None
        assert evidence._resistance["TEST"].boundary_ms == 301_000
        assert evidence._resistance["TEST"].close_int == 100_000
        boundary = 302_000
        at = market_day_boundary(session, boundary)
        await evidence.observe_completed_seconds(StrategyOneBoundaryWork(
            boundary,
            (("TEST", {1_000: {**bars[1], "session_date": session.isoformat(),
                               "boundary_ms": boundary}}),), ()))
        candidate = StrategyOneDecisionCandidate(
            {"session_date": session.isoformat(), "ticker": "TEST",
             "boundary_ms": boundary, "resolution_ms": 100,
             "price_valid": 1, "quote_valid": 1,
             "quote_timestamp_us": round(at.timestamp() * 1_000_000),
             "bid_int": 101_900, "ask_int": 102_000, "close_int": 102_000},
            StrategyOneEntryCursor(boundary, "TEST", 0, 301_000,
                                   (302_000, 300_000, 300_000, 300_000),
                                   300_000, 99_000))
        result = await evidence.entry_evidence(candidate, tick=.01)
        assert result.activation == frozen
        assert client.bar_reads == 1
        assert evidence.bos._states["TEST"].boundary_ms == 302_000
        assert result.bos.open_break is not None
        assert result.bos.open_break.boundary_ms == boundary
        assert result.bos_support is None
        assert result.protection is None

    asyncio.run(run())
    assert client.bar_reads == 1


def test_management_breaks_use_only_prior_known_completed_second():
    session = date(2026, 8, 18)
    level = {"unified_level_id": "r1", "lower": 10., "upper": 10.2,
             "role": "resistance"}

    class ReadyV7:
        prefetches_seconds = False

        def has_stream(self, ticker):
            return ticker == "TEST"

        def advance_seconds(self, rows, *, at):
            assert len(rows) == 1 and at.tzinfo is not None

        def strategy_one_levels(self, ticker, *, as_of):
            assert ticker == "TEST" and as_of.tzinfo is not None
            return (level,)

    evidence = object.__new__(StrategyOneCausalEvidence)
    evidence.session = session
    evidence.v7 = ReadyV7()
    evidence._resistance = {}
    evidence._completed_breaks = {}
    evidence._break_boundary_ms = 0
    evidence._completed_30s = {}

    async def observe(boundary, opened=None, closed=None):
        resolutions = ({1_000: {
            "session_date": session.isoformat(), "ticker": "TEST",
            "boundary_ms": boundary, "resolution_ms": 1_000,
            "open_int": opened, "close_int": closed, "price_valid": 1}}
            if opened is not None else {})
        await evidence.observe_completed_seconds(StrategyOneBoundaryWork(
            boundary, (("TEST", resolutions),) if resolutions else (), ()))

    async def run():
        await observe(1_000, 100_000, 100_500)
        assert evidence.completed_resistance_breaks("TEST", boundary_ms=1_000) == ()
        await observe(2_000, 100_500, 101_500)
        breaks = evidence.completed_resistance_breaks("TEST", boundary_ms=2_000)
        assert len(breaks) == 1
        assert breaks[0].level["unified_level_id"] == "r1"
        await observe(2_100)
        assert evidence.completed_resistance_breaks("TEST", boundary_ms=2_100) == ()

    asyncio.run(run())


def test_management_low_expires_and_invalid_completed_bucket_revokes_it():
    session = date(2026, 8, 18)
    evidence = object.__new__(StrategyOneCausalEvidence)
    evidence.session = session
    evidence.v7 = type("NoLoadedBooks", (), {"has_stream": lambda _self, _ticker: False})()
    evidence._resistance = {}
    evidence._completed_breaks = {}
    evidence._break_boundary_ms = 0
    evidence._completed_30s = {}

    level_reads = []

    async def levels(_ticker, _boundary):
        level_reads.append((_ticker, _boundary))
        return ({"unified_level_id": "r1", "lower": 11., "upper": 11.1,
                 "role": "resistance"},)

    evidence._levels = levels

    async def observe(boundary, low=None, *, valid=True):
        rows = ({30_000: {"session_date": session.isoformat(),
                           "ticker": "TEST", "boundary_ms": boundary,
                           "resolution_ms": 30_000, "low_int": low,
                           "price_valid": int(valid),
                           "extremes_valid": int(valid)}} if low is not None else {})
        await evidence.observe_completed_seconds(StrategyOneBoundaryWork(
            boundary, (("TEST", rows),) if rows else (), ()))

    async def run():
        await observe(30_000, 98_000)
        assert evidence.completed_30s_low("TEST", boundary_ms=30_000)["low_int"] == 98_000
        now_us = round(market_day_boundary(session, 30_000).timestamp() * 1_000_000)
        quote = {"session_date": session.isoformat(), "ticker": "TEST",
                 "boundary_ms": 30_000, "resolution_ms": 100,
                 "price_valid": 1, "quote_valid": 1,
                 "quote_timestamp_us": now_us,
                 "bid_int": 100_000, "ask_int": 100_100}
        joined = await evidence.management_evidence(
            "TEST", {100: quote}, boundary_ms=30_000)
        assert (joined.bid, joined.ask, joined.low_boundary_ms,
                joined.low_int) == (10., 10.01, 30_000, 98_000)
        assert len(joined.overhead_levels) == 1
        assert level_reads == [("TEST", 30_000)]
        quote_only = await evidence.management_evidence(
            "TEST", {100: {**quote, "price_valid": 0}}, boundary_ms=30_000)
        assert (quote_only.bid, quote_only.ask, quote_only.low_int) == (
            10., 10.01, 98_000)
        assert quote_only.overhead_levels == ()
        assert level_reads == [("TEST", 30_000)]
        await observe(59_900)
        assert evidence.completed_30s_low("TEST", boundary_ms=59_900)["low_int"] == 98_000
        stale = await evidence.management_evidence(
            "TEST", {100: {**quote, "boundary_ms": 59_900}},
            boundary_ms=59_900)
        assert stale.bid is None and stale.ask is None
        assert stale.low_int == 98_000 and stale.overhead_levels == ()
        await observe(60_000, 95_000, valid=False)
        assert evidence.completed_30s_low("TEST", boundary_ms=60_000) is None
        await observe(60_100)
        assert evidence.completed_30s_low("TEST", boundary_ms=60_100) is None

    asyncio.run(run())
