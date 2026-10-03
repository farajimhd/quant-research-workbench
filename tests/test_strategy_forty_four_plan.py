from datetime import timedelta
from types import SimpleNamespace
import json
import pytest

import src.backend.backtest_strategy_forty_four_plan as planning
from scripts.clickhouse.publish_strategy_forty_three_history import publication_sources
from src.backend.backtest_market_data import market_day_boundary
from tests.test_strategy_forty_four_source import fixture


def test_native_episodes_are_price_filtered_before_first_eligible_second(monkeypatch):
    _, arguments = fixture(monkeypatch)
    market = arguments["market"]
    monkeypatch.setattr(planning, "certified_market_plan_from_arte", lambda **_: market)
    monkeypatch.setattr(planning, "project_market_day_plan", lambda value, _: value)
    monkeypatch.setattr(planning, "certify_identity_plan", lambda *_a, **_k: arguments["identity"])
    listing = dict(listing_id="L", symbol_id="S", security_id="I")
    monkeypatch.setattr(planning, "certified_reference_members", lambda *_: ("e" * 64, {"TEST": listing}))
    monkeypatch.setattr(planning, "canonical_stream_activation", lambda: ({}, {}))
    anchor = market_day_boundary(market.sessions[0], 0)
    episodes = [dict(ticker="TEST", last_price=price,
                     available_at=(anchor + timedelta(milliseconds=ms)).isoformat())
                for price, ms in ((.5, 100), (10., 1200), (11., 2000))]
    monkeypatch.setattr(planning, "load_first_squeeze_occurrences", lambda *_a, **_k:
        dict(occurrences=episodes, authority=dict(query_sha256="f" * 64)))
    monkeypatch.setattr(planning, "certified_seed_plan", lambda *_: SimpleNamespace(token="g" * 64))
    monkeypatch.setattr(planning, "certify_v7_interval_plan", lambda *_a, **_k: arguments["structure"])
    plan = planning.certify_source_plan(session=market.sessions[0], build=market.build_id, reader=object())
    assert plan.admissions["TEST"] == 2000
    sources = publication_sources(plan)
    assert sources == publication_sources(plan)
    assert len(sources) == 1 and sources[0].admission_ms == 2000
    assert sources[0].source_market_token == market.token
    with pytest.raises(ValueError, match="premarket only"):
        planning.certify_source_plan(session=market.sessions[0], build=market.build_id,
                                    reader=object(), session_end_ms=10_000)


def test_reference_integrity_is_checked_before_selected_listing_exclusion(monkeypatch):
    _, arguments = fixture(monkeypatch)
    pin = SimpleNamespace(snapshot_id="snapshot", population_source_hash="123", available_at="2026-09-03 07:59:00")
    monkeypatch.setattr(planning, "load_reference_pin", lambda *_: pin)
    class Reader:
        def execute(self, query):
            assert "is_tradable=1" not in query
            return json.dumps(dict(hash="124"))
    with pytest.raises(RuntimeError, match="full reference snapshot differs"):
        planning.certified_reference_members(Reader(), arguments["market"], arguments["identity"])
