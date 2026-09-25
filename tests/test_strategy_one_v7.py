"""Strategy 1 V7 policy is pinned by Backtest preflight, not legacy gates."""
from datetime import datetime, timezone

import pytest

from src.market_engine.derived_trade_policy import POLICY
from src.market_engine.streaming_level_book import VERSION
from src.trading_runtime.strategy_one_v7 import (
    PROVISIONAL_SEED_POLICY, admitted_v7_levels,
)


AT = datetime.fromtimestamp(1_800_000_000, timezone.utc)


def context(seed=PROVISIONAL_SEED_POLICY):
    return {
        "qmd_level_book_version": VERSION,
        "v7_input_policy": POLICY,
        "v7_seed_input_policy": seed,
        "v7_max_input_timestamp": AT.timestamp() - .5,
        "qmd_structure_unified_levels": [{
            "unified_level_id": "level-1", "book_version": VERSION,
            "input_policy": POLICY, "seed_input_policy": seed,
            "lower": 9.9, "upper": 10.1,
            "confirmed_at_ms": (AT.timestamp() - 2) * 1000,
        }],
    }


def test_approved_provisional_seed_is_admitted_only_when_plan_pins_it():
    row = admitted_v7_levels(context(), as_of=AT,
                             seed_policy=PROVISIONAL_SEED_POLICY)
    assert len(row) == 1
    with pytest.raises(ValueError, match="seed plan"):
        admitted_v7_levels(context(), as_of=AT, seed_policy=POLICY)
    assert len(admitted_v7_levels(context(POLICY), as_of=AT,
                                  seed_policy=POLICY)) == 1


def test_stale_or_future_v7_cannot_create_entry_geometry():
    stale = context()
    stale["v7_max_input_timestamp"] = AT.timestamp() - 1.1
    assert admitted_v7_levels(stale, as_of=AT,
                              seed_policy=PROVISIONAL_SEED_POLICY) == ()
    future = context()
    future["v7_max_input_timestamp"] = AT.timestamp() + .1
    with pytest.raises(ValueError, match="future"):
        admitted_v7_levels(future, as_of=AT,
                           seed_policy=PROVISIONAL_SEED_POLICY)
    future = context()
    future["qmd_structure_unified_levels"][0]["confirmed_at_ms"] = (
        AT.timestamp() + .1) * 1000
    with pytest.raises(ValueError, match="causal"):
        admitted_v7_levels(future, as_of=AT,
                           seed_policy=PROVISIONAL_SEED_POLICY)


def test_level_policy_mismatch_is_an_error_not_an_empty_signal():
    mixed = context()
    mixed["qmd_structure_unified_levels"][0]["seed_input_policy"] = POLICY
    with pytest.raises(ValueError, match="seed geometry"):
        admitted_v7_levels(mixed, as_of=AT,
                           seed_policy=PROVISIONAL_SEED_POLICY)


def test_empty_coverage_seed_uses_filtered_runtime_policy_even_in_v1_campaign():
    empty = context(POLICY)
    empty["qmd_structure_unified_levels"] = []
    assert admitted_v7_levels(empty, as_of=AT, seed_policy=POLICY) == ()
