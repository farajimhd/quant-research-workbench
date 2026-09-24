from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.trading_runtime.arte_grouped_resistance_projection import (
    TABLES, project_grouped_resistance_state, restore_grouped_resistance_state,
)
from src.trading_runtime.resistance_zones import observe


SNAPSHOT = "4a2c2393-d13d-4ecb-9fae-b5e26ea9ff6f"


def source_level(identity: str, lower: float, upper: float) -> dict:
    return {
        "unified_level_id": identity, "lower": lower, "upper": upper,
        "price": (lower + upper) / 2, "side": -1, "role": "resistance",
        "confirmed_at_ms": 1_776_000_000_000.0,
        "book_version": "causal-level-book-v7-mle-1",
        "input_policy": "causal", "seed_input_policy": "causal",
    }


def observed_state() -> dict:
    state: dict = {}
    at = datetime(2026, 9, 24, 14, 0, tzinfo=timezone.utc)
    rows = {"a": source_level("a", 10.0, 10.2),
            "b": source_level("b", 10.3, 10.5)}
    observe(SimpleNamespace(observed_at=at, price=9.0), state, rows)
    observe(SimpleNamespace(observed_at=at + timedelta(seconds=1), price=11.0), state, rows)
    assert state["broken"]
    return state


def test_normalized_zone_state_roundtrip_from_actual_producer() -> None:
    state = observed_state()
    rows = project_grouped_resistance_state(
        state, assignment_id="assignment-1", revision=47, snapshot_id=SNAPSHOT,
    )
    assert restore_grouped_resistance_state(rows) == state
    reversed_readback = deepcopy(rows)
    reversed_readback["level"].reverse()
    reversed_readback["member"].reverse()
    reversed_readback["broken"].reverse()
    assert restore_grouped_resistance_state(reversed_readback) == state
    assert {row["family"] for row in rows["level"]} == {"known", "physical", "break_rows"}
    assert [row["ordinal"] for row in rows["broken"]] == list(range(len(state["broken"])))
    assert all("storage_policy = 'live_market_ssd'" in table.ddl() for table in TABLES)
    assert all("JSON" not in kind and "Array" not in kind and "Map" not in kind
               for table in TABLES for _, kind in table.columns)


def test_zone_projection_and_readback_fail_closed_on_unknown_or_corrupt_rows() -> None:
    state = observed_state()
    state["known"][next(iter(state["known"]))]["unexpected"] = 1
    with pytest.raises(ValueError, match="unmodeled"):
        project_grouped_resistance_state(state, assignment_id="a", revision=47, snapshot_id=SNAPSHOT)
    state = observed_state()
    rows = project_grouped_resistance_state(state, assignment_id="a", revision=47, snapshot_id=SNAPSHOT)
    altered = deepcopy(rows)
    altered["level"][0]["lower"] = 8.0
    with pytest.raises(ValueError):
        restore_grouped_resistance_state(altered)
