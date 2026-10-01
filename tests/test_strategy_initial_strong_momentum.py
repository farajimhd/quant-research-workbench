"""Native first-setup freezing and typed scalar chronology/source authority."""
from dataclasses import replace
from uuid import uuid4

import numpy as np
import pytest

from src.trading_runtime.strategy_initial_strong_momentum import (
    InitialStrongMomentumWitness, initial_strong_momentum_entry_mask,
    initial_strong_momentum_entry, validate_initial_strong_momentum_witness,
    initial_strong_momentum_policy_payload,
)
from src.trading_runtime.strategy_rising_momentum_witness import (
    RisingMomentumWitness, CompletedMomentumObservation,
)


def witness(boundary, *, strong=True):
    current = float(2.0 if strong else 1.0)
    return RisingMomentumWitness('AAA', boundary, 'a' * 64,
        '00000000-0000-0000-0000-000000000001', 'b' * 64,
        tuple(CompletedMomentumObservation(resolution, boundary // resolution * resolution,
            boundary // resolution * resolution - resolution, current, 0.0, 1.0, 0.0)
            for resolution in (1000, 10000)))


def source():
    return [np.array([0, 0, 0, 1, 1, 0, 2], dtype=np.int64),
            np.array([30000, 30000, 30000, 30000, 30000, 40000, 30000], dtype=np.int64),
            np.array([30300, 30100, 30200, 30200, 30100, 40100, 30100], dtype=np.int64),
            np.array([True, False, True, True, True, True, False]),
            np.array([True, True, False, True, True, True, True])]


def test_unordered_native_groups_select_first_base_and_never_resurrect_weak_episode():
    arrays = source()
    first, eligible = initial_strong_momentum_entry_mask(*arrays)
    assert first.tolist() == [2, 2, 2, 4, 4, 5, -1]
    assert eligible.tolist() == [False, False, False, True, True, True, False]
    for output in (first, eligible):
        with pytest.raises(ValueError):
            output.setflags(write=True)
    assert not first.flags.writeable and not eligible.flags.writeable


def test_input_permutation_preserves_selected_keys_and_eligibility():
    arrays = source()
    first, eligible = initial_strong_momentum_entry_mask(*arrays)
    permutation = np.array([6, 3, 1, 5, 2, 0, 4])
    permuted_first, permuted_eligible = initial_strong_momentum_entry_mask(
        *(value[permutation] for value in arrays))
    restored = np.full(len(first), -1)
    restored[permutation] = np.where(permuted_first < 0, -1,
                                    permutation[np.maximum(permuted_first, 0)])
    np.testing.assert_array_equal(restored, first)
    np.testing.assert_array_equal(permuted_eligible, eligible[permutation])


def test_inputs_unchanged_and_empty_population_is_supported():
    arrays = source()
    originals = [value.copy() for value in arrays]
    initial_strong_momentum_entry_mask(*arrays)
    for actual, expected in zip(arrays, originals):
        np.testing.assert_array_equal(actual, expected)
    first, eligible = initial_strong_momentum_entry_mask(*(value[:0] for value in arrays))
    assert first.shape == eligible.shape == (0,)
    assert first.dtype == np.int64 and eligible.dtype == np.bool_


@pytest.mark.parametrize('index,value', [(0, -1), (1, 0), (1, 30001),
                                         (1, 30400), (2, 30101), (2, 57600100)])
def test_invalid_clocks_or_codes_reject(index, value):
    arrays = source()
    arrays[index][0] = value
    with pytest.raises(ValueError, match='aligned typed'):
        initial_strong_momentum_entry_mask(*arrays)


@pytest.mark.parametrize('index,dtype', [(0, np.int32), (1, np.float64),
                                       (2, object), (3, np.int64), (4, np.int8)])
def test_exact_native_types_and_shapes_required(index, dtype):
    arrays = source()
    arrays[index] = arrays[index].astype(dtype)
    with pytest.raises(ValueError, match='aligned typed'):
        initial_strong_momentum_entry_mask(*arrays)
    arrays = source()
    arrays[index] = arrays[index][:2]
    with pytest.raises(ValueError, match='aligned typed'):
        initial_strong_momentum_entry_mask(*arrays)


def test_duplicate_source_key_rejects_even_when_not_base_eligible():
    arrays = source()
    arrays[2][0] = arrays[2][2]
    arrays[3][0] = False
    with pytest.raises(ValueError, match='duplicated'):
        initial_strong_momentum_entry_mask(*arrays)


def test_scalar_requires_both_initial_and_current_strong_without_claiming_initiality():
    first = witness(30100)
    current = witness(40100)
    anchor = InitialStrongMomentumWitness(30000, first)
    validate_initial_strong_momentum_witness(current, anchor)
    assert initial_strong_momentum_entry(current, anchor)
    assert not initial_strong_momentum_entry(current, replace(anchor, first_setup=witness(30100, strong=False)))
    assert not initial_strong_momentum_entry(witness(40100, strong=False), anchor)
    # Scalar source consistency alone also accepts a later alleged first setup;
    # only the compiler can establish that the earlier candidate was initial.
    assert initial_strong_momentum_entry(current, replace(anchor, first_setup=current))


@pytest.mark.parametrize('field,value', [('ticker', 'BBB'), ('source_build_id', 'c' * 64),
                                        ('source_attempt_id', str(uuid4())), ('market_plan_token', 'd' * 64)])
def test_scalar_source_mismatches_are_integrity_errors(field, value):
    first, current = witness(30100), witness(40100)
    anchor = InitialStrongMomentumWitness(30000, first)
    with pytest.raises(ValueError, match='identity or causal'):
        initial_strong_momentum_entry(replace(current, **{field: value}), anchor)


@pytest.mark.parametrize('anchor', [InitialStrongMomentumWitness(30200, witness(30100)),
                                   InitialStrongMomentumWitness(30000, witness(50100)),
                                   InitialStrongMomentumWitness(True, witness(30100)), object()])
def test_scalar_invalid_types_and_future_first_setup_reject(anchor):
    with pytest.raises(ValueError):
        initial_strong_momentum_entry(witness(40100), anchor)


def test_policy_payload_is_detached_and_exposes_compiler_authority():
    policy = initial_strong_momentum_policy_payload()
    assert policy['first_setup'] == 'first_structurally_eligible_setup'
    assert policy['initiality_authority'] == 'certified_native_candidate_compiler'
    policy['first_setup'] = 'later'
    assert initial_strong_momentum_policy_payload()['first_setup'] != 'later'
