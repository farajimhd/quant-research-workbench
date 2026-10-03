from dataclasses import replace
import json
import struct

import pytest

from pipelines.strategy_one.strategy_forty_three_publication import prepare_rows
from src.backend.backtest_market_data import CertifiedMarketDayPlan, MarketDayUnit, ExecutionInterval
from src.backend.backtest_strategy_one_identity import CertifiedIdentityPlan
from src.backend.backtest_strategy_one_v7_interval_store import CertifiedV7IntervalPlan, CertifiedV7IntervalUnit
from src.backend.backtest_strategy_forty_three_source import certify_history
from src.trading_runtime.strategy_forty_three_fact_schema import FACT_COLUMNS
from tests.test_strategy_forty_three_publication import source
from tests.test_strategy_forty_three_facts import grid


def fixture(monkeypatch):
    import src.backend.backtest_strategy_forty_three_source as consumer
    origin = replace(source(), session_end_ms=30_000)
    facts, population, coverage = prepare_rows(origin, grid([10.] * 30))
    units = tuple(MarketDayUnit(origin.build_id, origin.session_date, origin.ticker,
        stage, attempt, "f" * 64, 30, "f" * 64)
        for stage, attempt in (("bars", origin.bars_attempt_id), ("broker_100ms", origin.liquidity_attempt_id)))
    market = CertifiedMarketDayPlan(ExecutionInterval.parse("100ms"), origin.build_id,
        "f" * 64, (origin.session_date,), (origin.ticker,), units, (100, 1000), origin.source_market_token)
    identity = CertifiedIdentityPlan(origin.build_id, origin.session_date, origin.attempt_id,
        market.token, (origin.ticker,), (origin.conid,), "f" * 64, "f" * 64)
    unit = CertifiedV7IntervalUnit(origin.ticker, origin.attempt_id, origin.bars_attempt_id,
        "f" * 64, "f" * 64, "f" * 64, "f" * 64, "filtered", 0, 0, "f" * 64, "f" * 64)
    structure = CertifiedV7IntervalPlan(origin.build_id, origin.session_date,
        (unit,), ((origin.ticker, ()),), ((origin.ticker, ()),), origin.source_v7_token)
    state = dict(facts=facts, population=population, coverage=coverage)
    class Reader:
        def execute(self, sql):
            assert sql.startswith("SELECT ")
            if "fact_coverage" in sql:
                rows = [state["coverage"]]
            elif "population_v1" in sql:
                rows = [state["population"]]
            else:
                rows = []
                for row in state["facts"]:
                    converted = dict(row)
                    for name, kind in FACT_COLUMNS:
                        if "Float64" in kind:
                            value = converted.pop(name)
                            converted[name + "_bits"] = None if value is None else struct.unpack("<Q", struct.pack("<d", value))[0]
                    rows.append(converted)
            return "\n".join(json.dumps(row) for row in rows)
    monkeypatch.setattr(consumer, "verify_tables", lambda _: None)
    arguments = dict(market=market, identity=identity, structure=structure,
        candidate_tickers=(origin.ticker,), snapshot_hash=origin.source_snapshot_hash,
        signal_query_hash=origin.source_signal_query_hash, session_end_ms=origin.session_end_ms,
        reader=Reader())
    return state, arguments


def test_consumer_selects_exact_seal_and_exposes_immutable_completed_features(monkeypatch):
    state, arguments = fixture(monkeypatch)
    plan = certify_history(**arguments)
    assert plan.feature("TEST", 6000)["fact_id"] == state["facts"][5]["fact_id"]
    assert len(plan.load_active_history("TEST", arguments["reader"])) == 30
    state["facts"][12]["high"] = 12.
    with pytest.raises(RuntimeError, match="changed after"):
        plan.load_active_history("TEST", arguments["reader"])
    with pytest.raises(TypeError):
        plan.feature("TEST", 6000)["close"] = 99.
    with pytest.raises(ValueError):
        plan.feature("TEST", 6500)


def test_late_signal_remains_in_certified_population_even_when_entry_cutoff_will_reject_it(monkeypatch):
    from src.trading_runtime.strategy_forty_three_source_codec import scalar_hash
    state, arguments = fixture(monkeypatch)
    state["population"]["admission_ms"] = 30_000
    state["coverage"]["population_hash"] = scalar_hash((state["population"],))
    plan = certify_history(**arguments)
    assert plan.feature("TEST", 30_000)["fact_id"] == state["facts"][-1]["fact_id"]


@pytest.mark.parametrize("mutation", ["children", "parent", "identity", "population", "missing"])
def test_consumer_rejects_changed_or_incomplete_authority(monkeypatch, mutation):
    state, arguments = fixture(monkeypatch)
    if mutation == "children":
        state["facts"][0]["close"] = 10.000000000000002
    elif mutation == "parent":
        state["coverage"]["source_v7_token"] = "0" * 64
    elif mutation == "identity":
        arguments["identity"] = replace(arguments["identity"], conids=(321,))
    elif mutation == "population":
        state["population"]["admission_ms"] = 7000
    else:
        state["facts"].pop()
    with pytest.raises(RuntimeError):
        certify_history(**arguments)
