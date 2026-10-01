"""Strict native/scalar staged Strategy19 momentum contract and causal source checks."""
from dataclasses import replace
from uuid import uuid4

import numpy as np
import pytest

from src.trading_runtime.strategy_initial_momentum_growth import (
    POLICY_ID, FIRST_SETUP_GROWTH_FRACTION, first_setup_momentum_growth_entry_mask,
    first_setup_momentum_growth_entry, first_setup_momentum_growth_policy_payload,
)
from src.trading_runtime.strategy_rising_momentum_witness import (
    CompletedMomentumObservation, RisingMomentumWitness,
)


def arrays(histograms, previous):
    count = len(histograms)
    boundary = np.full(count, 30_100, dtype=np.int64)
    current = np.tile([30_000, 30_000], (count, 1))
    prior = np.tile([29_000, 20_000], (count, 1))
    line = np.column_stack((np.full(count, 100.0), histograms)).astype(np.float64)
    old_line = np.column_stack((np.zeros(count), previous)).astype(np.float64)
    return [boundary, current, prior, line, np.zeros((count, 2)),
            old_line, np.zeros((count, 2))]


def witness(source, index):
    observations = tuple(CompletedMomentumObservation(
        resolution, int(source[1][index, branch]), int(source[2][index, branch]),
        *(None if np.isnan(source[field][index, branch]) else float(source[field][index, branch])
          for field in (3, 4, 5, 6))) for branch, resolution in enumerate((1000, 10000)))
    return RisingMomentumWitness('AAA', int(source[0][index]), 'a' * 64,
                                  str(uuid4()), 'b' * 64, observations)


def test_strict_threshold_positive_negative_and_zero_previous():
    source = arrays([1.5, np.nextafter(1.5, np.inf), 0.0, 0.1, -0.8, 0.1],
                    [1.0, 1.0, 0.0, 0.0, -1.0, -1.0])
    assert first_setup_momentum_growth_entry_mask(*source).tolist() == [False, True, False, True, False, True]
    for index, expected in enumerate((False, True, False, True, False, True)):
        assert first_setup_momentum_growth_entry(witness(source, index)) is expected


def test_only_ten_second_branch_can_qualify_and_missing_rejects():
    source = arrays([0.1], [1.0])
    assert not first_setup_momentum_growth_entry_mask(*source)[0]
    source[2][0, 1] = 0
    source[5][0, 1] = source[6][0, 1] = np.nan
    assert not first_setup_momentum_growth_entry_mask(*source)[0]
    assert not first_setup_momentum_growth_entry(witness(source, 0))
    source[5][0, 1] = 0.0
    with pytest.raises(ValueError, match='invented'):
        first_setup_momentum_growth_entry_mask(*source)


@pytest.mark.parametrize('field,clock', [(1, 40_000), (1, 20_000), (2, 10_000)])
def test_forming_stale_or_nonadjacent_source_is_rejected(field, clock):
    source = arrays([2.0], [1.0])
    source[field][0, 1] = clock
    with pytest.raises(ValueError, match='forming, stale or nonadjacent'):
        first_setup_momentum_growth_entry_mask(*source)


@pytest.mark.parametrize('dtype', [np.float32, np.int64, object])
def test_exact_float64_source_precision_is_required(dtype):
    source = arrays([2.0], [1.0])
    source[3] = source[3].astype(dtype)
    with pytest.raises(ValueError, match='aligned'):
        first_setup_momentum_growth_entry_mask(*source)


def test_finite_histogram_and_growth_threshold_overflows_fail_closed():
    maximum = np.finfo(np.float64).max
    source = arrays([maximum], [maximum])
    with pytest.raises(ValueError, match='overflows'):
        first_setup_momentum_growth_entry_mask(*source)
    with pytest.raises(ValueError, match='overflows'):
        first_setup_momentum_growth_entry(witness(source, 0))
    source[4][0, 1] = -maximum
    with pytest.raises(ValueError, match='overflows'):
        first_setup_momentum_growth_entry_mask(*source)


def test_native_scalar_prefix_consistency_and_input_immutability():
    source = arrays([0.1, 2.0, 1.0, -1.0], [0.0, 1.0, 1.0, -2.0])
    before = [value.copy() for value in source]
    full = first_setup_momentum_growth_entry_mask(*source)
    assert full.tolist() == [True, True, False, False]
    np.testing.assert_array_equal(full[:2], first_setup_momentum_growth_entry_mask(
        *(value[:2] for value in source)))
    for index in range(len(full)):
        assert bool(full[index]) is first_setup_momentum_growth_entry(witness(source, index))
    for value, original in zip(source, before):
        np.testing.assert_array_equal(value, original)
    assert first_setup_momentum_growth_entry_mask(*(value[:0] for value in source)).shape == (0,)


def test_scalar_witness_requires_exact_identity_and_finite_values():
    source = arrays([2.0], [1.0])
    value = witness(source, 0)
    for changed in (replace(value, ticker='aaa'), replace(value, source_build_id=str(uuid4())),
                    replace(value, observations=value.observations[::-1])):
        with pytest.raises(ValueError):
            first_setup_momentum_growth_entry(changed)
    with pytest.raises(ValueError):
        first_setup_momentum_growth_entry(object())


def test_policy_payload_is_explicit_and_detached():
    policy = first_setup_momentum_growth_policy_payload()
    assert policy['policy_id'] == POLICY_ID
    assert policy['fraction'] == FIRST_SETUP_GROWTH_FRACTION == 0.50
    assert policy['resolution_ms'] == 10_000
    policy['fraction'] = 0.0
    assert first_setup_momentum_growth_policy_payload()['fraction'] == 0.50



def test_first_setup_50pct_does_not_replace_current_entry_10pct():
    from src.trading_runtime.strategy_strong_ten_second_momentum import strong_ten_second_momentum_entry_mask
    source = arrays([1.2, 1.5, np.nextafter(1.5, np.inf)], [1.0, 1.0, 1.0])
    assert strong_ten_second_momentum_entry_mask(*source).tolist() == [True, True, True]
    assert first_setup_momentum_growth_entry_mask(*source).tolist() == [False, False, True]


def test_new_threshold_overflow_even_when_old_threshold_is_finite():
    maximum = np.finfo(np.float64).max
    source = arrays([maximum], [maximum * .75])
    from src.trading_runtime.strategy_strong_ten_second_momentum import strong_ten_second_momentum_entry_mask
    assert strong_ten_second_momentum_entry_mask(*source)[0]
    with pytest.raises(ValueError, match='50pct.*overflows'):
        first_setup_momentum_growth_entry_mask(*source)
    with pytest.raises(ValueError, match='50pct.*overflows'):
        first_setup_momentum_growth_entry(witness(source, 0))


def test_future_primitive_is_not_an_installed_strategy():
    from src.trading_runtime.numbered_fixed_strategy import numbered_fixed_strategy
    with pytest.raises(ValueError, match='No installed'):
        numbered_fixed_strategy(20)


def test_available_rejected_current_still_rejects_threshold_overflow():
    maximum = np.finfo(np.float64).max
    source = arrays([1.0], [maximum * .75])
    from src.trading_runtime.strategy_strong_ten_second_momentum import strong_ten_second_momentum_entry_mask
    assert not strong_ten_second_momentum_entry_mask(*source)[0]
    with pytest.raises(ValueError, match='50pct.*overflows'):
        first_setup_momentum_growth_entry_mask(*source)


def test_exact_premarket_cutoff_and_afterhours_keep_current_ten_percent():
    from src.trading_runtime.strategy_initial_momentum_growth import PREMARKET_END_MS
    source = arrays([1.2, 1.2, 1.2], [1.0, 1.0, 1.0])
    source[0][:] = [PREMARKET_END_MS - 100, PREMARKET_END_MS, PREMARKET_END_MS + 100]
    for column, resolution in enumerate((1000, 10000)):
        source[1][:, column] = source[0] // resolution * resolution
        source[2][:, column] = source[1][:, column] - resolution
    assert first_setup_momentum_growth_entry_mask(*source).tolist() == [False, True, True]
    for index, expected in enumerate((False, True, True)):
        assert first_setup_momentum_growth_entry(witness(source, index)) is expected
    policy = first_setup_momentum_growth_policy_payload()
    assert policy['session_scope'] == 'premarket_only'
    assert policy['afterhours_first_setup_policy'] == 'unchanged_strict_10pct'
    assert policy['boundary_semantics'] == 'first_structurally_eligible_setup_boundary_ms'


def test_out_of_scope_new_threshold_overflow_does_not_reject():
    maximum = np.finfo(np.float64).max
    source = arrays([maximum], [maximum * .75])
    source[0][0] = 45_000_100
    source[1][0] = [45_000_000, 45_000_000]
    source[2][0] = [44_999_000, 44_990_000]
    assert first_setup_momentum_growth_entry_mask(*source)[0]
    assert first_setup_momentum_growth_entry(witness(source, 0))
