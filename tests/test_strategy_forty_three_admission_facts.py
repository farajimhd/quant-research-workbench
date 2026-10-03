from dataclasses import replace
from types import MappingProxyType
from datetime import datetime, timezone

import pytest
import src.backend.backtest_strategy_forty_three_facts as reader
from src.backend.backtest_market_data import market_day_boundary
from src.backend.backtest_strategy_forty_three_plan import FortyThreeSourcePlan
from src.backend.backtest_strategy_forty_three_source import certify_history
from tests.test_strategy_forty_three_source import fixture


def test_decision_uses_only_completed_signal_second_and_existing_cumulative_vwap(monkeypatch):
    state, args = fixture(monkeypatch)
    history = certify_history(**args)
    market = replace(args["market"], units=(*args["market"].units,
        replace(args["market"].units[0], stage="technical")))
    from src.trading_runtime.strategy_one_v7 import PROVISIONAL_SEED_POLICY
    structure = replace(args["structure"], coverage=tuple(replace(unit,
        seed_input_policy=PROVISIONAL_SEED_POLICY) for unit in args["structure"].coverage))
    population = state["population"]
    plan = FortyThreeSourcePlan(market, args["identity"], None, structure,
        args["snapshot_hash"], args["signal_query_hash"], MappingProxyType({"TEST": 6000}),
        MappingProxyType({"TEST": population}), 30_000)
    delta = market_day_boundary(plan.market.sessions[0], 0) - datetime(1970, 1, 1, tzinfo=timezone.utc)
    origin = (delta.days * 86400 + delta.seconds) * 1_000_000
    rows = [dict(ticker="TEST", resolution_ms=100, boundary_ms=5500,
        quote_valid=1, bid_int=99_800, ask_int=100_000, quote_timestamp_us=origin + 5_499_999,
        last_event_us=origin + 5_499_999, execution_volume=100., execution_vwap=9.5),
        dict(ticker="TEST", resolution_ms=100, boundary_ms=6000,
        quote_valid=0, bid_int=0, ask_int=0, quote_timestamp_us=0,
        last_event_us=origin + 5_999_999, execution_volume=50., execution_vwap=9.6)]
    requested = []
    def fetch(market, _client, **kwargs):
        requested.append(kwargs["candidate_boundaries"])
        return iter(rows)
    monkeypatch.setattr(reader, "iter_market_day_rows", fetch)
    class BarReader:
        def execute(self, query):
            assert "resolution_ms=1000" in query and "(ticker,attempt_id,bucket_index) IN" in query
            assert ",14405)" in query and "FORMAT JSONEachRow" in query
            return '{"ticker":"TEST","trade_count":7}'
    facts, = reader.load_admission_facts(plan, history, BarReader())
    assert facts.bid == 9.98 and facts.ask == 10.
    assert facts.quote_age_us == 500_001 and facts.quote_valid
    assert facts.vwap == 9.6 and facts.trades == 7 and facts.volume == 150.
    assert requested == [{"TEST": tuple(range(5100, 6001, 100))}]
    corrupted = replace(plan, admissions=MappingProxyType({"TEST": 7000}))
    with pytest.raises(RuntimeError, match="first signal"):
        reader.load_admission_facts(corrupted, history, object())
