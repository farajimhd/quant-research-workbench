"""Causal source-boundary and missing-data tests for proposed momentum rule."""
import numpy as np
import pytest

from src.trading_runtime.strategy_rising_momentum_entry import rising_momentum_entry_mask


def source():
    boundaries = np.array([10_100, 20_100, 30_100], dtype=np.int64)
    current = np.array([[10_000, 10_000], [20_000, 20_000], [30_000, 30_000]])
    prior = current - np.array([1000, 10000])
    prior[0, 1] = 0
    line = np.array([[2., 2.], [2., 2.], [2., 2.]])
    signal = np.array([[1., 1.], [1., 1.], [1., 1.]])
    old_line = np.array([[3., np.nan], [3., 1.5], [3., 3.]])
    old_signal = np.array([[1., np.nan], [1., 1.], [1., 1.]])
    return [boundaries, current, prior, line, signal, old_line, old_signal]


def test_either_resolution_can_qualify_but_flat_or_falling_rejects():
    arrays = source()
    assert rising_momentum_entry_mask(*arrays).tolist() == [False, True, False]
    arrays[5][0, 0] = 1.5
    assert rising_momentum_entry_mask(*arrays).tolist() == [True, True, False]
    arrays[5][0, 0] = 2.
    assert not rising_momentum_entry_mask(*arrays)[0]  # Equal histogram rejects.


@pytest.mark.parametrize('array_index,value', [(1, 21_000), (2, 18_000)])
def test_forming_or_nonadjacent_bucket_is_integrity_error(array_index, value):
    arrays = source()
    arrays[array_index][1, 0] = value
    with pytest.raises(ValueError, match='forming, stale or nonadjacent'):
        rising_momentum_entry_mask(*arrays)


def test_missing_adjacent_bucket_does_not_carry_older_or_zero_values():
    arrays = source()
    arrays[2][1, 1] = 0
    arrays[5][1, 1] = arrays[6][1, 1] = np.nan
    assert not rising_momentum_entry_mask(*arrays)[1]
    arrays[5][1, 1] = arrays[6][1, 1] = 0.
    with pytest.raises(ValueError, match='invented values'):
        rising_momentum_entry_mask(*arrays)


def test_future_tail_does_not_change_prefix_and_inputs_are_unchanged():
    arrays = source()
    snapshots = [x.copy() for x in arrays]
    complete = rising_momentum_entry_mask(*arrays)
    assert np.array_equal(complete[:2], rising_momentum_entry_mask(*(x[:2] for x in arrays)))
    for actual, before in zip(arrays, snapshots):
        np.testing.assert_array_equal(actual, before)


def test_empty_batch_and_nonfinite_or_wrong_shape_fail_closed():
    arrays = source()
    assert rising_momentum_entry_mask(*(x[:0] for x in arrays)).shape == (0,)
    arrays[3][0, 0] = np.inf
    with pytest.raises(ValueError, match='aligned'):
        rising_momentum_entry_mask(*arrays)
    arrays = source()
    arrays[4] = arrays[4][:, :1]
    with pytest.raises(ValueError, match='aligned'):
        rising_momentum_entry_mask(*arrays)


def test_finite_source_values_cannot_overflow_into_a_positive_signal():
    arrays = source()
    arrays[3][1, 1] = np.finfo(np.float64).max
    arrays[4][1, 1] = -np.finfo(np.float64).max
    with pytest.raises(ValueError, match='overflows'):
        rising_momentum_entry_mask(*arrays)
