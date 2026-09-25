from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json

import pytest

from src.trading_runtime.arte_market_day_certification import (
    TABLES, family_hash, prepare_market_day_certificate, verify_market_day_certificate,
)


BUILD = "a" * 64
PIN = "b" * 64
DAY = "2026-08-18"


class FakeReader:
    def __init__(self, tables):
        self.tables = tables
        self.calls = []

    def execute(self, sql):
        assert sql.startswith("SELECT ") and "FORMAT JSONEachRow" in sql
        self.calls.append(sql)
        name = sql.split("FROM arte.", 1)[1].split(" ", 1)[0]
        return "\n".join(json.dumps(row) for row in self.tables[name])


def inventory():
    names = [table.name for table in TABLES]
    header = [dict(build_id=BUILD, definition_hash=PIN, version="market-day-core-v5",
                   calculation_source_hash=PIN, rules_hash=PIN, source_plan_hash=PIN,
                   scope_count=1, scope_hash="")]
    scopes = [dict(build_id=BUILD, session_date=DAY, ticker="TEST",
                   source_event_count=1, first_ordinal=1, last_ordinal=1,
                   population_snapshot_id="snapshot", population_revision="preopen-tradable-snapshot-v3",
                   population_available_at="2026-08-18T07:00:00+00:00",
                   population_cutoff_at="2026-08-18T08:00:00+00:00",
                   population_source_hash="123")]
    stages = [dict(build_id=BUILD, session_date=DAY, ticker="TEST", stage=stage,
                   attempt_id="attempt", source_hash="source", output_rows=0,
                   output_hash="0") for stage in ("bars", "technical", "broker_100ms")]
    seeds = [dict(build_id=BUILD, session_date=DAY, ticker="TEST", attempt_id="attempt",
                  mode=0, predecessor_date="", prior_build_id="", prior_state_hash="")]
    header[0]["scope_hash"] = family_hash(scopes)
    fence = [dict(build_id=BUILD, definition_hash=PIN, header_hash=family_hash(header),
                  scope_count=1, scope_hash=family_hash(scopes), stage_count=3,
                  stage_hash=family_hash(stages), seed_count=1, seed_hash=family_hash(seeds))]
    return dict(zip(names, (header, scopes, stages, seeds, fence)))


def test_zero_row_planned_unit_is_certified_by_explicit_stage_rows() -> None:
    client = FakeReader(inventory())
    proof = verify_market_day_certificate(client, BUILD, sessions=(DAY,))
    assert proof.scopes == ((DAY, "TEST"),)
    assert len(proof.stages) == 3
    assert all(stage[4:6] == (0, "0") for stage in proof.stages)
    assert len(client.calls) == 5


def test_planned_scope_with_no_source_events_remains_certifiable() -> None:
    tables = inventory()
    scope = tables["market_day_planned_scope_v1"][0]
    scope.update(source_event_count=0, first_ordinal=0, last_ordinal=0)
    header = tables["market_day_build_header_v1"][0]
    fence = tables["market_day_build_fence_v1"][0]
    header["scope_hash"] = family_hash(tables["market_day_planned_scope_v1"])
    fence["scope_hash"] = header["scope_hash"]
    fence["header_hash"] = family_hash(tables["market_day_build_header_v1"])
    assert verify_market_day_certificate(FakeReader(tables), BUILD,
                                         sessions=(DAY,)).scopes == ((DAY, "TEST"),)


@pytest.mark.parametrize("family", [
    "market_day_build_header_v1", "market_day_planned_scope_v1",
    "market_day_stage_certificate_v1", "market_day_seed_v1",
    "market_day_build_fence_v1",
])
def test_duplicate_certificate_rows_fail_closed(family) -> None:
    tables = inventory()
    tables[family].append(deepcopy(tables[family][0]))
    with pytest.raises(RuntimeError):
        verify_market_day_certificate(FakeReader(tables), BUILD, sessions=(DAY,))


def test_missing_stage_and_missing_fence_fail_closed() -> None:
    tables = inventory()
    tables["market_day_stage_certificate_v1"].pop()
    with pytest.raises(RuntimeError, match="inventory"):
        verify_market_day_certificate(FakeReader(tables), BUILD, sessions=(DAY,))
    tables = inventory()
    tables["market_day_build_fence_v1"].clear()
    with pytest.raises(RuntimeError, match="final fence"):
        verify_market_day_certificate(FakeReader(tables), BUILD, sessions=(DAY,))


def test_conflicting_stage_and_seed_hashes_fail_closed() -> None:
    tables = inventory()
    tables["market_day_stage_certificate_v1"][0]["source_hash"] = "changed"
    with pytest.raises(RuntimeError, match="final fence"):
        verify_market_day_certificate(FakeReader(tables), BUILD, sessions=(DAY,))
    tables = inventory()
    tables["market_day_seed_v1"][0]["attempt_id"] = "other"
    with pytest.raises(RuntimeError, match="seed provenance"):
        verify_market_day_certificate(FakeReader(tables), BUILD, sessions=(DAY,))


def test_population_time_and_unrequested_session_fail_closed() -> None:
    tables = inventory()
    tables["market_day_planned_scope_v1"][0]["population_available_at"] = "2026-08-18T09:00:00+00:00"
    with pytest.raises(RuntimeError, match="population"):
        verify_market_day_certificate(FakeReader(tables), BUILD, sessions=(DAY,))
    with pytest.raises(RuntimeError, match="Requested session"):
        verify_market_day_certificate(FakeReader(inventory()), BUILD,
                                      sessions=("2026-08-19",))


def test_producer_preparation_preserves_zero_row_scope_without_writing() -> None:
    plan = dict(requested=[DAY], units=[dict(source_date=DAY, ticker="TEST",
        event_count=1, next_ordinal=2, last_ordinal=1)], population=[dict(
        session_date=DAY, certificate=dict(snapshot_id="snapshot",
        revision="preopen-tradable-snapshot-v3", source_hash=123,
        available_at_utc="2026-08-18T07:00:00+00:00",
        cutoff_utc="2026-08-18T08:00:00+00:00"))])
    definition = dict(version="market-day-core-v5", plan=plan,
                      calculation_source=PIN, rules_hash=PIN)
    build_id = sha256(json.dumps(definition, sort_keys=True, separators=(",", ":"),
                                 default=str).encode()).hexdigest()

    class Ledger:
        def unit(self, build, day, ticker, stage):
            return dict(status="complete", attempt_id="attempt", source_hash="source",
                        output_rows=0, output_hash="0")

        def seed(self, build, day, ticker):
            return dict(attempt_id="attempt", mode=0, predecessor_date="",
                        prior_build_id="", prior_state_hash="")

    prepared = prepare_market_day_certificate(definition, build_id, Ledger())
    assert verify_market_day_certificate(FakeReader(prepared), build_id,
                                         sessions=(DAY,)).scopes == ((DAY, "TEST"),)

    class MissingStage(Ledger):
        def unit(self, build, day, ticker, stage):
            return None if stage == "bars" else super().unit(build, day, ticker, stage)

    with pytest.raises(ValueError, match="lacks completed bars"):
        prepare_market_day_certificate(definition, build_id, MissingStage())
