"""Activation gap is frozen once at its causal signal price and V7 book."""
import pytest

from src.trading_runtime.strategy_one_activation_state import (
    ActivationCatalog, freeze_strategy_one_activation,
)


def level(identity, price):
    return {"unified_level_id": identity, "lower": price - .01,
            "upper": price + .01, "role": "resistance", "side": "resistance"}


def test_gap_is_frozen_at_activation_not_later_candidate_geometry():
    rows = [level("r1", 11.), level("r2", 12.), level("r3", 13.)]
    frozen = freeze_strategy_one_activation(
        session_date="2026-08-18", ticker="ABCD",
        boundary_ms=1_000, price_int=100_000, admitted_levels=rows)
    assert frozen.average_gap == 1.
    assert frozen.resistance_ids == ("r1", "r2", "r3")
    catalog = ActivationCatalog()
    catalog.add(frozen)
    rows[1]["lower"] = 20.
    assert catalog.get("ABCD", 1_000).average_gap == 1.
    with pytest.raises(ValueError, match="already frozen"):
        catalog.add(frozen)


def test_no_levels_at_activation_cannot_be_filled_from_future():
    frozen = freeze_strategy_one_activation(
        session_date="2026-08-18", ticker="ABCD",
        boundary_ms=1_000, price_int=100_000, admitted_levels=())
    assert frozen.average_gap is None
    assert frozen.resistance_ids == ()
    catalog = ActivationCatalog()
    catalog.add(frozen)
    with pytest.raises(ValueError, match="lacks frozen activation"):
        catalog.get("ABCD", 2_000)
