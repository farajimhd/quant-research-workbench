import math
from pathlib import Path

import polars as pl

from .schedule import FLOOR, UNIQUE_FLOOR, candidate_prefix, cosine_rate, order_for_pass
from ..phase2_v2.release import ReleasedUnits


def test_pass_rates_and_boundary():
    for index in range(10):
        start = 1e-4 * .9**index
        assert math.isclose(cosine_rate(1e-4, index, 0, 100), start)
        assert math.isclose(cosine_rate(1e-4, index, 50, 100), (start + FLOOR) / 2)
        assert cosine_rate(1e-4, index, 100, 100) == FLOOR


def test_unique_pass_rates():
    for index in range(20):
        peak = 1e-4 * .9**(index + 1)
        assert math.isclose(cosine_rate(1e-4, index, 0, 100, unique=True), peak)
        assert math.isclose(cosine_rate(1e-4, index, 50, 100, unique=True), (peak + UNIQUE_FLOOR) / 2)
        assert cosine_rate(1e-4, index, 100, 100, unique=True) == 5e-7


def test_pass_cursor_and_shuffle():
    rows = pl.DataFrame({
        "path": [f"shards/ticker/{n}" for n in range(12)],
        "ordinal": [[0], [0, 1]] * 6,
        "unit_split": [0] * 10 + [1, 1],
    })
    units = ReleasedUnits([(Path("."), "binding", rows)])
    assert order_for_pass(units, 0) is units
    assert candidate_prefix(units, 2, 7) == 3*4096+7
    assert candidate_prefix(units, len(units)) == 15*4096
    second = order_for_pass(units, 1)
    ordered = second.groups[0][2]
    assert ordered["path"].to_list() != rows["path"].to_list()
    assert ordered.tail(2).equals(rows.tail(2))
    assert ordered.equals(order_for_pass(units, 1).groups[0][2])
    assert candidate_prefix(second, len(second)) == candidate_prefix(units,len(units))
