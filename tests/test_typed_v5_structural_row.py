from copy import deepcopy
from dataclasses import replace

import pytest

from src.market_engine.swing_book_v6 import StreamingSwingBookV6
from src.market_engine.swing_book_v5 import StreamingSwingBookV5
from src.trading_runtime import v5_breakout as V
from src.trading_runtime.typed_v5_structural_row import validate_typed_v5_structural_row
from tests.test_swing_book_v6 import found
from tests.test_v5_breakout import market, parameters


def _source_row():
    book = StreamingSwingBookV6(opening=0.)
    found(book, 104., "resistance")
    return next(row for row in book.snapshot()["unified_levels"] if row["side"] == -1)


def test_real_v6_producer_row_is_closed_and_typed_observer_admits_it():
    row = _source_row()
    assert validate_typed_v5_structural_row(row) == row
    observation = replace(market(), structural_resistance_levels=(row,))
    p = parameters()
    assert V.rows(observation, p, typed_persistence=True) == [row]
    state = {}
    V.observe(observation, p, state, typed_persistence=True)
    assert state["v5_breakout_state"]["levels"] == [row]


def test_real_v5_producer_row_is_closed():
    book = StreamingSwingBookV5(opening=0.)
    found(book, 104., "resistance")
    row = next(row for row in book.snapshot()["unified_levels"] if row["side"] == -1)
    assert validate_typed_v5_structural_row(row) == row


def test_typed_only_rejects_extra_or_untyped_without_legacy_regression():
    row = _source_row()
    observation = replace(market(), structural_resistance_levels=(dict(row, surprise={"x": 1}),))
    p = parameters()
    assert V.rows(observation, p)[0]["surprise"] == {"x": 1}
    with pytest.raises(ValueError, match="unmodeled"):
        V.observe(observation, p, {}, typed_persistence=True)
    bad = deepcopy(row)
    bad["selection_members"] = [{"nested": "not typed"}]
    with pytest.raises(ValueError, match="string list"):
        validate_typed_v5_structural_row(bad)
    bad = dict(row, lower=None)
    with pytest.raises(ValueError, match="bounds"):
        validate_typed_v5_structural_row(bad)
