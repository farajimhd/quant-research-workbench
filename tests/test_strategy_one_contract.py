"""The new number is isolated from historical Strategy 350 behavior."""
from src.trading_runtime.strategy_one_contract import (
    STRATEGY_NUMBER, ordinal_target, outside_swing_stop,
    resistance_group_stop, upward_stop_update,
)


def swing(price, pivot, confirmed, scale):
    return dict(price=price, pivot_at=pivot, confirmed_at=confirmed,
                scale=scale, side="support", state="active")


def resistance(identifier, midpoint):
    return dict(unified_level_id=identifier, lower=midpoint-.01,
                upper=midpoint+.01, side="resistance", role="resistance")


def test_first_outside_swing_and_local_fallback_are_causal():
    assert STRATEGY_NUMBER == 1
    local = [swing(10, 90, 92, "local")]
    major = [swing(9.4, 80, 83, "major"), swing(9.7, 85, 88, "major"),
             swing(9.9, 87, 101, "major")]
    result = outside_swing_stop(local_swings=local, major_swings=major,
                                now=100, tick=.01)
    assert result["source"] == "first_outside_swing_low"
    assert result["selected"]["price"] == 9.7
    assert result["price"] == 9.69
    fallback = outside_swing_stop(local_swings=local, major_swings=[],
                                  now=100, tick=.01)
    assert fallback["source"] == "local_swing_low"
    assert fallback["price"] == 9.99
    assert outside_swing_stop(local_swings=[], major_swings=major,
                              now=100, tick=.01) is None


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
