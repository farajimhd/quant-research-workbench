from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.trading_runtime.arte_long_momentum_squeeze_v7_evidence import (
    SET_TABLE, TABLES, project_v7_evidence, project_v7_evidence_set,
    restore_v7_evidence, restore_v7_evidence_set,
)
from src.trading_runtime.vwap_resistance_ladder import levels


IDENTITY = dict(run_id="run-1", assignment_id="assignment-1",
                state_revision=9, snapshot_session="2026-09-24")


def _level():
    raw = dict(unified_level_id="v7-1", band_lower=10.0, band_upper=10.2,
               price=10.1, side=-1, role="resistance", confirmed_at_ms=1000.0,
               book_version="causal-level-book-v7-mle-1", input_policy="canonical",
               seed_input_policy="canonical")
    observation = SimpleNamespace(
        observed_at=datetime(2026, 9, 24, tzinfo=timezone.utc),
        structural_support_levels=(), structural_resistance_levels=(raw,),
        structural_transition_levels=(),
    )
    return levels(observation)["v7-1"]


@pytest.mark.parametrize("family,evidence", [
    ("level", _level()),
    ("pivot", dict(unified_level_id="pivot-1", side="support", lower=9.8,
                   upper=10.0, price=9.9, pivot_at=100.0, confirmed_at=101.0,
                   confirmed_at_ms=101000.0, book_version="causal-level-book-v7-mle-1",
                   scale="local", prominence=0.2, score=0.8)),
    ("structure_clock", dict(as_of=101.0, max_input_timestamp=100.0)),
])
def test_real_level_shape_and_closed_leaf_roundtrip(family, evidence):
    row = project_v7_evidence(family, evidence, source_path="squeeze_breakout.frozen_gap.levels",
                              ordinal=0, **IDENTITY)
    assert restore_v7_evidence(family, row) == evidence
    ddl = TABLES[family].ddl()
    assert "PARTITION BY toYYYYMM(snapshot_session)" in ddl
    assert "storage_policy = 'live_market_ssd'" in ddl
    assert not any(token in ddl for token in (" Array(", " Map(", " JSON", " payload "))


@pytest.mark.parametrize("family,evidence", [
    ("level", {**_level(), "unknown_geometry": 1}),
    ("pivot", dict(price=10.0, pivot_at=101.0, confirmed_at=100.0, side=1)),
    ("structure_clock", dict(as_of=100.0, max_input_timestamp=101.0)),
])
def test_unknown_or_noncausal_producer_evidence_rejected(family, evidence):
    with pytest.raises(ValueError):
        project_v7_evidence(family, evidence, source_path="squeeze_breakout.test",
                            ordinal=0, **IDENTITY)


def test_tampered_leaf_and_unknown_row_column_rejected():
    row = project_v7_evidence("level", _level(), source_path="squeeze_entry.anchor",
                              ordinal=0, **IDENTITY)
    for mode in ("value", "extra", "presence"):
        broken = deepcopy(row)
        if mode == "value":
            broken["upper"] = 99.0
        elif mode == "extra":
            broken["unknown"] = 1
        else:
            broken["role_present"] = False
        with pytest.raises(ValueError):
            restore_v7_evidence("level", broken)


def test_complete_level_set_roundtrip_and_partial_child_rejected():
    evidence = [_level(), {**_level(), "unified_level_id": "v7-2", "lower": 10.3,
                           "upper": 10.5}]
    projected = project_v7_evidence_set("level", evidence,
                                        source_path="squeeze_breakout.frozen_gap.levels",
                                        **IDENTITY)
    assert restore_v7_evidence_set(projected) == evidence
    assert "PARTITION BY toYYYYMM(snapshot_session)" in SET_TABLE.ddl()
    incomplete = deepcopy(projected)
    incomplete["rows"].pop()
    with pytest.raises(ValueError):
        restore_v7_evidence_set(incomplete)
    assert restore_v7_evidence_set(project_v7_evidence_set(
        "level", [], source_path="squeeze_breakout.frozen_gap.levels", **IDENTITY)) == []
