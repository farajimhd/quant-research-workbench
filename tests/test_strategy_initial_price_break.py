"""Causal/precision boundaries of staged first-setup price evidence."""
from dataclasses import replace

import numpy as np
import pytest

from src.trading_runtime.strategy_initial_price_break import (
    FirstSetupPriceBreakWitness, first_setup_price_break,
    first_setup_price_break_mask,
)


def witness(**changes):
    return replace(FirstSetupPriceBreakWitness(
        'ABC', 30_100, 'a' * 64, '00000000-0000-0000-0000-000000000001',
        'b' * 64, 30_000, 29_000, 101, 100, True, True), **changes)


def arrays(rows):
    names = ('first_setup_boundary_ms', 'current_boundary_ms', 'prior_boundary_ms',
             'current_close_int', 'prior_high_int', 'current_price_valid',
             'prior_extremes_valid')
    types = (np.int64, np.int64, np.int64, np.uint64, np.uint64, np.bool_, np.bool_)
    return tuple(np.asarray([getattr(row, name) for row in rows], dtype=dtype)
                 for name, dtype in zip(names, types))


def test_completed_break_is_strict_and_preserves_integer_precision():
    assert first_setup_price_break(witness())
    assert not first_setup_price_break(witness(current_close_int=100))
    assert not first_setup_price_break(witness(current_close_int=99))
    assert first_setup_price_break(witness(current_close_int=2**53+1, prior_high_int=2**53))
    assert first_setup_price_break(witness(current_close_int=2**64-1, prior_high_int=2**64-2))


def test_cutoff_and_afterhours_do_not_require_price_evidence():
    rows = [witness(first_setup_boundary_ms=clock, current_boundary_ms=0,
                    prior_boundary_ms=0, current_close_int=0, prior_high_int=0,
                    current_price_valid=False, prior_extremes_valid=False)
            for clock in (19_799_900, 19_800_000, 43_200_100, 57_600_000)]
    assert first_setup_price_break_mask(*arrays(rows)).tolist() == [False, True, True, True]
    assert [first_setup_price_break(row) for row in rows] == [False, True, True, True]


@pytest.mark.parametrize('change', [
    {'current_boundary_ms': 31_000},  # Forming/future completed bar.
    {'current_boundary_ms': 29_000},  # Older valid bar cannot replace current.
    {'prior_boundary_ms': 28_000},  # Nonadjacent predecessor cannot be carried.
    {'current_boundary_ms': 0},  # Missing must not retain price/valid flag.
    {'prior_boundary_ms': 0},
    {'current_close_int': 0},
    {'prior_high_int': 0},
    {'first_setup_boundary_ms': 30_150},
    {'first_setup_boundary_ms': 0},
])
def test_malformed_source_clocks_or_encodings_fail_closed(change):
    with pytest.raises(ValueError, match='completed source|completed first'):
        first_setup_price_break(witness(**change))


def test_missing_adjacent_bar_rejects_and_never_carries_older_price():
    assert not first_setup_price_break(witness(prior_boundary_ms=0,
        prior_high_int=0, prior_extremes_valid=False))
    assert not first_setup_price_break(witness(current_price_valid=False))
    assert not first_setup_price_break(witness(prior_extremes_valid=False))
    assert not first_setup_price_break(witness(first_setup_boundary_ms=100,
        current_boundary_ms=0, prior_boundary_ms=0, current_close_int=0,
        prior_high_int=0, current_price_valid=False, prior_extremes_valid=False))


@pytest.mark.parametrize('change', [
    {'ticker': 'abc'}, {'source_build_id': 'z'*64}, {'market_plan_token': 'c'*63},
    {'bars_attempt_id': '1'},
    {'bars_attempt_id': '00000000-0000-0000-0000-000000000000'},
    {'current_close_int': 101.0},
    {'prior_high_int': -1}, {'current_close_int': 2**64},
    {'current_price_valid': 1}, {'first_setup_boundary_ms': True},
])
def test_scalar_requires_original_exact_identity_and_types(change):
    with pytest.raises(ValueError, match='exact'):
        first_setup_price_break(witness(**change))


def test_native_rejects_float_prices_bad_shapes_and_non_boolean_flags():
    original = arrays([witness()])
    for index, replacement in ((3, original[3].astype(np.float64)),
                               (5, original[5].astype(np.uint8)),
                               (0, np.asarray([30_100, 30_200], dtype=np.int64))):
        changed = list(original)
        changed[index] = replacement
        with pytest.raises(ValueError, match='aligned exact'):
            first_setup_price_break_mask(*changed)


def test_native_empty_and_noncontiguous_rows_are_immutable_without_input_mutation():
    rows = [witness(current_close_int=price) for price in (101, 100, 99, 105)]
    original = arrays(rows)
    copies = tuple(value.copy() for value in original)
    result = first_setup_price_break_mask(*(value[::2] for value in original))
    assert result.tolist() == [True, False]
    for actual, expected in zip(original, copies):
        assert np.array_equal(actual, expected)
    with pytest.raises(ValueError):
        result.setflags(write=True)
    empty = first_setup_price_break_mask(*arrays([]))
    assert empty.shape == (0,) and empty.dtype == np.bool_


def test_later_break_cannot_resurrect_original_first_setup():
    from src.trading_runtime.strategy_initial_strong_momentum import initial_strong_momentum_entry_mask
    # Unsorted rows belong to the same ticker/episode. The original earliest
    # structural setup failed price confirmation; later strength cannot reset it.
    rows = [witness(first_setup_boundary_ms=40_100, current_boundary_ms=40_000,
                    prior_boundary_ms=39_000, current_close_int=130, prior_high_int=110),
            witness(current_close_int=90, prior_high_int=100)]
    first, parent = initial_strong_momentum_entry_mask(
        np.asarray([0, 0], dtype=np.int64), np.asarray([30_000, 30_000], dtype=np.int64),
        np.asarray([40_100, 30_100], dtype=np.int64),
        np.asarray([True, True]), np.asarray([True, True]))
    prices = first_setup_price_break_mask(*arrays(rows))
    assert prices.tolist() == [True, False]
    assert first.tolist() == [1, 1]
    assert (parent & prices[first]).tolist() == [False, False]


def test_twenty_is_not_yet_an_installed_runtime():
    from src.trading_runtime.numbered_fixed_strategy import numbered_fixed_strategy
    with pytest.raises(ValueError, match='No installed'):
        numbered_fixed_strategy(20)
