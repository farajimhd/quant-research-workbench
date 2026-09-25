from copy import deepcopy

import pytest

from src.trading_runtime.arte_assignment_vwap_swing import (
    TABLES, project_entry_swing, restore_entry_swing,
    validate_typed_entry_swing,
)
from src.trading_runtime.vwap_resistance_ladder import levels, support_swing
from src.trading_runtime.arte_assignment_state_composite import project_modeled_assignment_state
from tests.test_arte_assignment_state_composite import KEY, _state
from tests.test_vwap_resistance_ladder import fixture


IDENTITY = {key: KEY[key] for key in ("assignment_id", "revision", "snapshot_id", "session")}


def _swing():
    return dict(side="support", lower=9.48, upper=9.5, price=9.49,
                pivot_at=100.0, confirmed_at=102.0,
                unified_level_id="swing-1", book_version="causal-level-book-v7-mle-1",
                scale="local", prominence=0.2, score=7)


def _support():
    return dict(unified_level_id="support-1", lower=9.48, upper=9.5,
                price=9.49, side=1, confirmed_at_ms=100000.0,
                book_version="causal-level-book-v7-mle-1",
                input_policy="causal", seed_input_policy="causal")


@pytest.mark.parametrize("with_bounce", [False, True])
def test_vwap_swing_named_parent_and_optional_bounce_cold_readback(with_bounce):
    swing = _swing()
    if with_bounce:
        swing["support_bounce"] = dict(pivot_at=100.0, pivot_price=9.49,
                                       recovered_at=103.0, support=_support())
    rows = project_entry_swing(swing, **IDENTITY)
    class FakeStorage:
        def read(self, table):
            return deepcopy(rows["swing" if table == TABLES[0].name else "bounce"])
    fake = FakeStorage()
    assert restore_entry_swing({"swing": fake.read(TABLES[0].name),
                                "bounce": fake.read(TABLES[1].name)}) == swing
    assert rows["swing"]["score_int"] == 7
    assert rows["swing"]["prominence_float"] == 0.2
    assert all("JSON" not in kind and "Map" not in kind
               for table in TABLES for _, kind in table.columns)


def test_absent_swing_and_full_entry_remains_unmodeled():
    assert restore_entry_swing(project_entry_swing(None, **IDENTITY)) is None
    with pytest.raises(ValueError, match="unmodeled"):
        project_modeled_assignment_state(
            {**_state(), "vwap_ladder_entry": {"swing": _swing()}}, **KEY)


def test_opt_in_actual_support_swing_producer_validation_preserves_default():
    _, _, observation = fixture()
    o = observation()
    source_levels = levels(o)
    assert support_swing(o, source_levels, 0) == support_swing(
        o, source_levels, 0, typed_persistence=True)
    o.structural_detector_state["row"]["local_swings"][0]["unknown"] = {"x": 1}
    assert "unknown" in support_swing(o, source_levels, 0)
    with pytest.raises(ValueError):
        support_swing(o, source_levels, 0, typed_persistence=True)


def test_actual_support_swing_bounce_variant_is_typed_and_roundtrips():
    _, _, observation = fixture()
    o = observation()
    pivot = o.structural_detector_state["row"]["local_swings"][0]
    support = levels(o)["support"]
    o.structural_detector_state["row"]["vwap_support_bounces"] = [
        dict(pivot_at=pivot["pivot_at"], pivot_price=pivot["price"],
             recovered_at=o.observed_at.timestamp() - 1, support=support)]
    swing = support_swing(o, levels(o), 0, typed_persistence=True)
    assert swing["support_bounce"]["support"] == support
    assert restore_entry_swing(project_entry_swing(swing, **IDENTITY)) == swing


@pytest.mark.parametrize("change", [
    {"unknown": 1}, {"side": "resistance"}, {"lower": float("nan")},
    {"pivot_at": 103.0}, {"score": {"not": "scalar"}},
    {"support_bounce": {"pivot_at": 100.0}},
])
def test_swing_rejects_unmodeled_source(change):
    with pytest.raises(ValueError):
        validate_typed_entry_swing({**_swing(), **change})


def test_swing_rejects_corrupt_bounce_and_identity():
    swing = _swing()
    swing["support_bounce"] = dict(pivot_at=100.0, pivot_price=9.49,
                                   recovered_at=103.0, support=_support())
    rows = project_entry_swing(swing, **IDENTITY)
    changed = deepcopy(rows)
    changed["bounce"]["support_lower_float"] = 9.47
    with pytest.raises(ValueError):
        restore_entry_swing(changed)
    changed = deepcopy(rows)
    changed["bounce"]["assignment_id"] = "other"
    with pytest.raises(ValueError):
        restore_entry_swing(changed)
