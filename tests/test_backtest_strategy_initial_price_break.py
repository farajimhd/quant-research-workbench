"""A later price break cannot replace the immutable first setup."""
from dataclasses import replace

import numpy as np
import pytest

from test_backtest_strategy_initial_momentum import plans
from test_backtest_strategy_initial_momentum_growth import StrongFirst, market_for
from src.backend.backtest_strategy_rising_momentum import load_rising_momentum_plan
from src.backend.backtest_strategy_initial_momentum_growth import compile_initial_momentum_growth_plan
from src.backend.backtest_strategy_initial_price_break import stage_initial_price_break_plan


def parent_plan():
    candidates, entry, _, _ = plans()
    momentum = load_rising_momentum_plan(market_for(candidates), candidates, client=StrongFirst())
    return compile_initial_momentum_growth_plan(candidates, entry, momentum)


def columns(first_close):
    return (np.array([31000, 41000], dtype=np.int64),
            np.array([30000, 40000], dtype=np.int64),
            np.array([first_close, 110], dtype=np.uint64),
            np.array([100, 100], dtype=np.uint64),
            np.array([True, True]), np.array([True, True]))


def test_later_price_break_does_not_resurrect_failed_first_setup():
    parent = parent_plan()
    assert parent.eligible_mask.tolist() == [True, True]
    staged = stage_initial_price_break_plan(parent, columns(100))
    assert staged.eligible_mask.tolist() == [False, False]
    assert parent.initial.first_indices.tolist() == [0, 0]


def test_seal_and_arrays_cannot_be_mutated_or_replaced():
    original = columns(101)
    staged = stage_initial_price_break_plan(parent_plan(), original)
    assert staged.eligible_mask.tolist() == [True, True]
    original[2][0] = 1
    assert staged.observations[2][0] == 101
    with pytest.raises(ValueError):
        staged.observations[2].setflags(write=True)
    with pytest.raises(ValueError, match='content seal'):
        replace(staged, token='f' * 64)
    with pytest.raises(ValueError, match='eligibility differs'):
        replace(staged, eligible_mask=np.array([True, False]))


def test_untyped_parent_and_misaligned_source_fail_closed():
    with pytest.raises(ValueError, match='exact certified'):
        stage_initial_price_break_plan(object(), columns(101))
    with pytest.raises(ValueError):
        stage_initial_price_break_plan(parent_plan(), columns(101)[:-1])
