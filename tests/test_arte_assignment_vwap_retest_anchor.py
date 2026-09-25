from copy import deepcopy

import pytest

from src.trading_runtime.arte_assignment_vwap_retest_anchor import (
    TABLES, project_entry_retest_anchor, restore_entry_retest_anchor,
    validate_typed_retest_anchor,
)
from src.trading_runtime.resistance_zones import entry_anchor
from tests.test_arte_assignment_state_composite import KEY
from tests.test_post_move_entries import pullback


IDENTITY = {key: KEY[key] for key in ("assignment_id", "revision", "snapshot_id", "session")}


def _witness(grouped=False):
    swing = dict(side="support", lower=3.91, upper=3.93, price=3.92,
                 pivot_at=100.0, confirmed_at=102.0)
    anchor = dict(unified_level_id="r1", lower=3.90, upper=3.97,
                  price=3.90, side=-1, confirmed_at_ms=99000.0,
                  book_version="causal-level-book-v7-mle-1",
                  input_policy="causal", seed_input_policy="causal",
                  broken_at=99.0)
    if grouped:
        anchor.update(unified_level_id="zone:r1", members=["r1", "r2"],
                      encountered=True, seen_below=True,
                      grouping_threshold=0.05)
    return dict(anchor=anchor, pivot_at=100.0, pivot_price=3.92,
                recovered_at=103.0, break_count=6, swing=swing)


@pytest.mark.parametrize("grouped", [False, True])
def test_passive_and_grouped_retest_exact_fake_cold_roundtrip(grouped):
    source = _witness(grouped)
    normalized = validate_typed_retest_anchor(source)
    rows = project_entry_retest_anchor(source, **IDENTITY)

    class FakeStorage:
        def read(self, table):
            return deepcopy(rows[{TABLES[0].name: "retest", TABLES[1].name: "members",
                                  TABLES[2].name: "swing", TABLES[3].name: "bounce"}[table]])

    fake = FakeStorage()
    assert restore_entry_retest_anchor({
        "retest": fake.read(TABLES[0].name), "members": fake.read(TABLES[1].name),
        "swing": fake.read(TABLES[2].name), "bounce": fake.read(TABLES[3].name),
    }) == normalized
    assert all("JSON" not in kind and "Map" not in kind
               for table in TABLES for _, kind in table.columns)


def test_actual_entry_anchor_typed_opt_in_preserves_legacy_default():
    _, assignment, observation = pullback()
    row = observation.structural_detector_state["row"]["vwap_retests"][0]
    row["anchor"].update(book_version="causal-level-book-v7-mle-1",
                         confirmed_at_ms=(row["pivot_at"] - 1) * 1000,
                         input_policy="causal", seed_input_policy="causal")
    market = assignment.state["vwap_ladder_market"]
    legacy = entry_anchor(observation, market, False, 6)
    typed = entry_anchor(observation, market, False, 6, typed_persistence=True)
    assert typed == validate_typed_retest_anchor(legacy)
    assert restore_entry_retest_anchor(project_entry_retest_anchor(typed, **IDENTITY)) == typed
    row["untyped"] = {"not": "closed"}
    assert "untyped" in entry_anchor(observation, market, False, 6)
    with pytest.raises(ValueError):
        entry_anchor(observation, market, False, 6, typed_persistence=True)


@pytest.mark.parametrize("change", [
    {"other": 1}, {"break_count": True}, {"recovered_at": 99.0},
    {"pivot_price": 3.91},
    {"anchor": {**_witness()["anchor"], "unknown": 1}},
    {"swing": {**_witness()["swing"], "unknown": 1}},
])
def test_retest_rejects_unmodeled_source(change):
    with pytest.raises(ValueError):
        validate_typed_retest_anchor({**_witness(), **change})


def test_retest_rejects_member_order_child_hash_and_mixed_identity():
    rows = project_entry_retest_anchor(_witness(True), **IDENTITY)
    changed = deepcopy(rows)
    changed["members"].reverse()
    with pytest.raises(ValueError):
        restore_entry_retest_anchor(changed)
    changed = deepcopy(rows)
    changed["swing"]["price_float"] = 3.91
    with pytest.raises(ValueError):
        restore_entry_retest_anchor(changed)
    changed = deepcopy(rows)
    changed["retest"]["assignment_id"] = "other"
    with pytest.raises(ValueError):
        restore_entry_retest_anchor(changed)
