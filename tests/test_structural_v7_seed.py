import json
from datetime import date

import pytest

from types import SimpleNamespace

from src.backend.structural_v7_seed import (
    _band_hash, certified_seed_plan, load_seed, preceding_coverage,
)
from src.market_engine.derived_trade_policy import POLICY
from src.market_engine.streaming_level_book import EXTRACTION_VERSION, StreamingLevelBook


class Client:
    def __init__(self, *, policy=POLICY, observations=3):
        self.queries = []
        self.coverage = dict(ticker="TEST", session_date="2026-08-17",
            available_at="2026-08-18 00:00:00.000000000", state="complete",
            level_count=1, observation_count=observations, input_policy=policy,
            source_extraction_version=EXTRACTION_VERSION,
            band_config_hash=_band_hash(), source_checkpoint_hash="a" * 64,
            source_plan_hash="b" * 64)

    def execute(self, sql):
        self.queries.append(sql)
        if "structural_level_coverage_v7" in sql:
            rows = [self.coverage]
        elif "structural_level_observations_v7" in sql:
            rows = [dict(observation_id=str(i).zfill(64), level_id="one",
                price=10.0 + i * .01, resolution=.01,
                at=f"2026-08-17 14:00:0{i}.000000000",
                resolved_at=f"2026-08-17 14:00:1{i}.000000000",
                role="support", session_date="2026-08-17") for i in range(3)]
        elif "structural_levels_v7" in sql:
            rows = [dict(level_id="one", origin_session="2026-08-17", price=10.01,
                lower=9.9, upper=10.1, association_radius=.2, lifecycle="qualified",
                historical=1, role="support", parent_level_id="", transition_from="",
                fit_status="estimated", fit_distribution="student_t", fit_count=3,
                fit_center=10.01, fit_scale=.05, fit_lower=9.9, fit_upper=10.1,
                fit_resolution=.01, fit_coverage=.8, fit_degrees_of_freedom=4.,
                fit_scale_at_floor=0)]
        else:
            raise AssertionError(sql)
        return "\n".join(json.dumps(row) for row in rows)


def test_typed_seed_is_accepted_by_streaming_engine():
    client = Client()
    seed = load_seed(client, ticker="TEST", session=date(2026, 8, 18))
    assert len(seed["levels"][0]["observations"]) == 3
    assert seed["source_checkpoint_hash"] == "a" * 64
    assert all(sql.lstrip().startswith("SELECT") for sql in client.queries)
    engine = StreamingLevelBook(seed, ticker="TEST", session="2026-08-18",
        start=1787039999.0, end=1787097600.0)
    assert len(engine.rows) == 1


def test_legacy_seed_is_explicitly_provisional():
    client = Client(policy="legacy-unfiltered")
    market = SimpleNamespace(build_id="market", sessions=("2026-08-18",),
        units=(SimpleNamespace(session_date="2026-08-18", ticker="TEST", stage="bars"),))
    assert certified_seed_plan(market, client).payload()["provisional"] is True
    seed = load_seed(client, ticker="TEST", session=date(2026, 8, 18))
    assert seed["input_policy"] == "legacy-unfiltered"


def test_unknown_seed_policy_fails_before_level_reads():
    client = Client(policy="unknown")
    with pytest.raises(ValueError, match="not compatible"):
        preceding_coverage(client, ticker="TEST", session=date(2026, 8, 18))
    assert len(client.queries) == 1


def test_published_initial_empty_day_seeds_an_empty_book():
    client = Client()
    client.coverage.update(state="empty", level_count=0, observation_count=0,
                           input_policy="", source_extraction_version="",
                           band_config_hash="0" * 64, source_checkpoint_hash="")
    original = client.execute
    def execute(sql):
        if "structural_levels_v7" in sql or "structural_level_observations_v7" in sql:
            client.queries.append(sql)
            return ""
        return original(sql)
    client.execute = execute
    seed = load_seed(client, ticker="TEST", session=date(2026, 8, 18))
    assert seed["levels"] == [] and seed["input_policy"] == POLICY


def test_missing_observation_fails_closed():
    client = Client(observations=4)
    with pytest.raises(ValueError, match="rows differ"):
        load_seed(client, ticker="TEST", session=date(2026, 8, 18))


def test_batched_preflight_pins_prior_filtered_seed():
    client = Client()
    market = SimpleNamespace(build_id="market", sessions=("2026-08-18",),
        units=(SimpleNamespace(session_date="2026-08-18", ticker="TEST", stage="bars"),))
    plan = certified_seed_plan(market, client)
    assert plan.payload()["unit_count"] == 1
    assert plan.payload()["catalog_hash"] == "b" * 64
    assert "LIMIT 1 BY ticker" in client.queries[0]
