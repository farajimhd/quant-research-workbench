"""The new number is isolated from historical Strategy 350 behavior."""
from src.trading_runtime.strategy_one_contract import (
    STRATEGY_NUMBER, closed_macd_candidate_mask, completed_30s_low_stop, ordinal_target,
    resistance_group_stop, upward_stop_update,
)


def resistance(identifier, midpoint):
    return dict(unified_level_id=identifier, lower=midpoint-.01,
                upper=midpoint+.01, side="resistance", role="resistance")


def test_completed_30s_low_is_causal_and_does_not_carry_empty_bucket():
    assert STRATEGY_NUMBER == 1
    arguments = dict(low_int=97_000, boundary_ms=30_000, now_ms=30_100,
                     tick=.01, price_valid=True, extremes_valid=True)
    result = completed_30s_low_stop(**arguments)
    assert result["source"] == "completed_30s_bar_low"
    assert result["price"] == 9.69
    assert completed_30s_low_stop(**{**arguments, "boundary_ms": 30_200}) is None
    assert completed_30s_low_stop(**{**arguments, "now_ms": 60_000}) is None
    assert completed_30s_low_stop(**{**arguments, "price_valid": False}) is None


def test_closed_macd_gate_is_vectorized_and_rejects_future_or_stale_values():
    import numpy as np
    lines = np.full((5, 4), .2)
    signals = np.full((5, 4), .1)
    source = np.tile([39_000, 35_000, 30_000, 30_000], (5, 1))
    source[1, 0] = 40_000  # A future completed second cannot be read at 39.9s.
    source[2, 3] = 0       # The prior 30s sample is stale by 39.9s.
    lines[3, 2] = np.nan
    signals[4, 1] = .2
    accepted = closed_macd_candidate_mask(lines, signals, source,
                                          np.full(5, 39_900))
    assert accepted.tolist() == [True, False, False, False, False]


def test_old_target_ordinals_and_never_lower():
    rows = [resistance(str(index), 10+index*.1) for index in range(1, 8)]
    assert ordinal_target(rows=rows, ask=10, tick=.01, broken_count=0)["price"] == 10.3
    assert ordinal_target(rows=rows, ask=10, tick=.01, broken_count=3)["ordinal"] == 3
    assert ordinal_target(rows=rows, ask=10, tick=.01, broken_count=4)["price"] == 10.2
    assert ordinal_target(rows=rows, ask=10, tick=.01, broken_count=6)["price"] == 10.1
    assert ordinal_target(rows=rows, ask=10, tick=.01, broken_count=6,
                          previous_target=10.3) is None


def test_disjoint_resistance_triples_and_stop_precedence():
    rows = [resistance(str(index), 10+index*.1) for index in range(1, 7)]
    first = resistance_group_stop(accepted_levels=rows[:3], applied_groups=0,
                                  tick=.01)
    assert first["group_level_ids"] == ["1", "2", "3"]
    assert first["price"] == 10.08
    assert resistance_group_stop(accepted_levels=rows[:3], applied_groups=1,
                                 tick=.01) is None
    second = resistance_group_stop(accepted_levels=rows, applied_groups=1,
                                   tick=.01)
    assert second["group_level_ids"] == ["4", "5", "6"]
    assert upward_stop_update(current=10, executable_bid=11,
        swing={"price": 10.2, "source": "swing"}, resistance=first)["source"] == (
            "three_resistance_step_stop")
    assert upward_stop_update(current=10.1, executable_bid=11,
        swing={"price": 10.2, "source": "swing"}, resistance=first)["source"] == "swing"
