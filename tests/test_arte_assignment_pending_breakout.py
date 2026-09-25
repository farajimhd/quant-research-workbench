from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.trading_runtime.arte_assignment_pending_breakout import (
    TABLES, project_pending_breakout, restore_pending_breakout,
    validate_pending_breakout,
)
from src.trading_runtime.post_move_entries import breakout_reference
from src.trading_runtime.vwap_resistance_ladder import levels


KEY = dict(assignment_id="assignment-1", revision=47,
           snapshot_id="4a2c2393-d13d-4ecb-9fae-b5e26ea9ff6f",
           session="2026-09-24")
BASE = dict(unified_level_id="r1", lower=100.0, upper=101.0,
            price=100.5, side=-1, role="resistance",
            confirmed_at_ms=1790261999000.0,
            book_version="causal-level-book-v7-mle-1",
            input_policy="causal", seed_input_policy="causal")


def _pending(anchor):
    return dict(anchor=anchor, trigger=101.1,
                witnessed_at="2026-09-24T14:00:00+00:00",
                target_price=103.0, episode_id=1790258400.0)


@pytest.mark.parametrize("anchor", [
    BASE,
    {**BASE, "unified_level_id": "zone:r1", "members": ["r1", "r2"],
     "encountered": True, "seen_below": True,
     "grouping_threshold": 0.25, "broken_at": 1790258401.0},
])
def test_passive_and_grouped_pending_breakout_exact_cold_roundtrip(anchor):
    normalized = validate_pending_breakout(_pending(anchor))
    rows = project_pending_breakout(_pending(anchor), **KEY)
    class FakeStorage:
        def __init__(self, projected):
            self.projected = deepcopy(projected)
        def read(self, table):
            return deepcopy(self.projected["parent"] if table == TABLES[0].name
                            else self.projected["members"])
    fake = FakeStorage(rows)
    assert restore_pending_breakout({"parent": fake.read(TABLES[0].name),
                                     "members": fake.read(TABLES[1].name)}) == normalized
    assert all("JSON" not in kind and "Map" not in kind
               for table in TABLES for _, kind in table.columns)


def test_typed_level_and_anchor_producers_fail_closed_without_changing_legacy():
    malformed = {**BASE, "role": {"untyped": True}}
    now = datetime(2026, 9, 24, 15, 0, tzinfo=timezone.utc)
    obs = SimpleNamespace(observed_at=now, structural_support_levels=[],
                          structural_resistance_levels=[malformed],
                          structural_transition_levels=[])
    assert levels(obs)["r1"]["role"] == {"untyped": True}
    with pytest.raises(ValueError):
        levels(obs, typed_persistence=True)
    market = {"known": {"r1": malformed}}
    assert breakout_reference(market, 103)["role"] == {"untyped": True}
    with pytest.raises(ValueError):
        breakout_reference(market, 103, typed_persistence=True)


@pytest.mark.parametrize("change", [
    {"anchor": {**BASE, "unknown": 1}},
    {"anchor": {**BASE, "price": float("nan")}},
    {"anchor": {**BASE, "side": "resistance"}},
    {"anchor": {**BASE, "members": ["r1"]}},
    {"target_price": 99.0},
    {"witnessed_at": "2026-09-24T10:00:00-04:00"},
    {"episode_id": None},
])
def test_unmodeled_pending_breakout_source_fails_closed(change):
    with pytest.raises(ValueError):
        project_pending_breakout({**_pending(BASE), **change}, **KEY)


def test_corrupt_member_order_and_parent_hash_rejected():
    anchor = {**BASE, "members": ["r1", "r2"], "encountered": True,
              "seen_below": False, "grouping_threshold": 0.25}
    rows = project_pending_breakout(_pending(anchor), **KEY)
    altered = deepcopy(rows)
    altered["members"].reverse()
    with pytest.raises(ValueError):
        restore_pending_breakout(altered)
    altered = deepcopy(rows)
    altered["parent"]["trigger_price"] = 101.2
    with pytest.raises(ValueError):
        restore_pending_breakout(altered)
