from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.trading_runtime.vwap_resistance_ladder import levels
from src.trading_runtime.typed_assignment_input import (
    CALLER_STATE_CONTRACT, STRUCTURAL_LEVEL_CONTRACT,
    validate_grouped_resistance_level, validate_typed_caller_assignment_state,
)


def level() -> dict:
    # The exact ten-field projection emitted by vwap_resistance_ladder.levels.
    return {
        "unified_level_id": "v7:ABC:1", "lower": 10.0, "upper": 10.2,
        "price": 10.1, "side": -1, "role": "resistance",
        "confirmed_at_ms": 1_776_000_000_000,
        "book_version": "causal-level-book-v7-mle-1",
        "input_policy": "causal", "seed_input_policy": "causal",
    }


def test_closed_v7_level_roundtrip_without_dropping_fields() -> None:
    assert STRUCTURAL_LEVEL_CONTRACT.endswith("-v1")
    source = level()
    restored = validate_grouped_resistance_level(source)
    assert restored == source and restored is not source
    restored["lower"] = 9.0
    assert source["lower"] == 10.0


def test_actual_grouped_resistance_source_projection_is_accepted() -> None:
    source = level()
    observation = SimpleNamespace(
        observed_at=datetime(2026, 9, 24, tzinfo=timezone.utc),
        structural_support_levels=(), structural_resistance_levels=(source,),
        structural_transition_levels=(),
    )
    projected = levels(observation)
    assert projected == {source["unified_level_id"]: source}
    assert validate_grouped_resistance_level(next(iter(projected.values()))) == source


@pytest.mark.parametrize("change", [
    {"unknown_metric": 1.0}, {"lower": float("nan")},
    {"upper": 9.0}, {"side": "resistance"},
    {"book_version": "v8"},
])
def test_closed_v7_level_rejects_unmodeled_or_untyped(change: dict) -> None:
    with pytest.raises(ValueError):
        validate_grouped_resistance_level({**level(), **change})


def test_caller_state_roundtrip_and_legacy_dynamic_state_rejection() -> None:
    assert CALLER_STATE_CONTRACT.endswith("-v1")
    state = {"campaign_id": "c1", "campaign_book_id": "default", "campaign_side": "long"}
    assert validate_typed_caller_assignment_state(state) == state
    assert validate_typed_caller_assignment_state(None) == {}
    with pytest.raises(ValueError, match="unmodeled"):
        validate_typed_caller_assignment_state({**state, "v7_setup": {"held": {}}})
    with pytest.raises(ValueError, match="campaign_side"):
        validate_typed_caller_assignment_state({"campaign_side": "both"})
