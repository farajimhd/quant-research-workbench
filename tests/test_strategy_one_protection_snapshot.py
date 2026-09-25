"""Typed Strategy 1 recovery rows have no opaque checkpoint escape hatch."""
from dataclasses import replace
from datetime import date
import json

import pytest

from src.trading_runtime.strategy_one_position import (
    AcceptedResistance, ProtectionState, ResistanceBreak, advance_protection,
)
from src.trading_runtime.strategy_one_protection_snapshot import (
    TABLES, ProtectionSnapshotRows, project_protection_snapshot,
    restore_protection_snapshot, load_protection_snapshot,
)


IDENTITY = ("paper", "AAPL", "position-1")


def _rows(state=None):
    if state is None:
        state = ProtectionState(
            31_000, 9.78, 10.3,
            frozenset({"first", "second", "third", "fourth"}),
            (AcceptedResistance("fourth", 9.94, 9.96),),
            (AcceptedResistance("first", 9.79, 9.81),
             AcceptedResistance("second", 9.89, 9.91),
             AcceptedResistance("third", 10.09, 10.11)),
            1, 1)
    return project_protection_snapshot(
        run_id="run-1", session_date=date(2026, 8, 18),
        checkpoint_sequence=42, boundary_ms=31_100,
        positions={IDENTITY: state})


def test_normalized_snapshot_roundtrips_position_owned_groups():
    rows = _rows()
    restored = restore_protection_snapshot(rows)
    assert restored[IDENTITY].accepted_ids == frozenset(
        {"first", "second", "third", "fourth"})
    assert restored[IDENTITY].earned_group[0].unified_level_id == "first"
    assert restored[IDENTITY].pending_group[0].unified_level_id == "fourth"
    assert rows.snapshot["position_count"] == 1
    assert rows.snapshot["resistance_count"] == 4
    assert restore_protection_snapshot(replace(
        rows, resistances=tuple(reversed(rows.resistances)))) == restored
    assert all("json" not in name and "blob" not in name
               for table in TABLES for name, _ in table.columns)
    assert all("live_market_ssd" in table.ddl() for table in TABLES)


def test_empty_snapshot_explicitly_seals_no_active_positions():
    rows = project_protection_snapshot(
        run_id="run-1", session_date=date(2026, 8, 18),
        checkpoint_sequence=42, boundary_ms=31_100, positions={})
    assert restore_protection_snapshot(rows) == {}
    assert rows.snapshot["position_count"] == rows.snapshot["resistance_count"] == 0


def test_tampered_state_or_missing_resistance_fails_cold_recovery():
    rows = _rows()
    state = {**rows.states[0], "stop": "9.790000000000000000"}
    with pytest.raises(ValueError, match="state or children"):
        restore_protection_snapshot(replace(rows, states=(state,)))
    with pytest.raises(ValueError, match="seal differs"):
        restore_protection_snapshot(replace(rows, resistances=rows.resistances[:-1]))
    with pytest.raises(ValueError, match="identity differs"):
        restore_protection_snapshot(replace(
            rows, snapshot={**rows.snapshot, "boundary_ms": 31_200}))


def test_snapshot_rejects_group_count_or_future_boundary():
    original = restore_protection_snapshot(_rows())[IDENTITY]
    with pytest.raises(ValueError, match="inconsistent"):
        _rows(replace(original, earned_groups=2))
    with pytest.raises(ValueError, match="inconsistent"):
        _rows(replace(original, boundary_ms=31_200))


def test_cold_restored_state_produces_identical_next_amendment():
    before = restore_protection_snapshot(_rows())[IDENTITY]
    after = restore_protection_snapshot(_rows(before))[IDENTITY]
    inputs = dict(now_ms=32_000, bid=10.4, ask=10.41, tick=.01,
                  low_boundary_ms=30_000, low_int=97_000,
                  low_price_valid=True, low_extremes_valid=True,
                  breaks=(ResistanceBreak(32_000, {
                      "unified_level_id": "fifth", "lower": 10.14,
                      "upper": 10.16, "side": "resistance", "role": "resistance"}),),
                  overhead_levels=(), price_bearing_bar=False)
    assert advance_protection(before, **inputs) == advance_protection(after, **inputs)


def test_read_only_loader_requires_exact_seal_and_typed_children():
    rows = _rows()

    class Reader:
        def __init__(self, seal):
            self.seal = seal
            self.queries = []

        def execute(self, query):
            self.queries.append(query)
            if "protection_snapshot_v1" in query:
                selected = () if self.seal is None else (self.seal,)
            elif "protection_state_v1" in query:
                selected = rows.states
            else:
                selected = rows.resistances
            return "\n".join(json.dumps(item) for item in selected)

    reader = Reader(rows.snapshot)
    assert load_protection_snapshot(reader, run_id="run-1",
                                    checkpoint_sequence=42)[IDENTITY].stop == 9.78
    assert len(reader.queries) == 3
    assert all(query.startswith("SELECT ") and "INSERT" not in query
               for query in reader.queries)
    assert "toString(stop) AS stop" in reader.queries[1]
    with pytest.raises(RuntimeError, match="exactly one sealed"):
        load_protection_snapshot(Reader(None), run_id="run-1",
                                 checkpoint_sequence=42)
