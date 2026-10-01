"""Strict native/scalar Strategy17 momentum contract and causal source checks."""
from dataclasses import replace
from uuid import uuid4

import numpy as np
import pytest

from src.trading_runtime.strategy_strong_ten_second_momentum import (
    POLICY_ID, HISTOGRAM_GROWTH_FRACTION, strong_ten_second_momentum_entry_mask,
    strong_ten_second_momentum_entry, strong_ten_second_momentum_policy_payload,
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
    source = arrays([1.1, np.nextafter(1.1, np.inf), 0.0, 0.1, -0.8, 0.1],
                    [1.0, 1.0, 0.0, 0.0, -1.0, -1.0])
    assert strong_ten_second_momentum_entry_mask(*source).tolist() == [False, True, False, True, False, True]
    for index, expected in enumerate((False, True, False, True, False, True)):
        assert strong_ten_second_momentum_entry(witness(source, index)) is expected


def test_only_ten_second_branch_can_qualify_and_missing_rejects():
    source = arrays([0.1], [1.0])
    assert not strong_ten_second_momentum_entry_mask(*source)[0]
    source[2][0, 1] = 0
    source[5][0, 1] = source[6][0, 1] = np.nan
    assert not strong_ten_second_momentum_entry_mask(*source)[0]
    assert not strong_ten_second_momentum_entry(witness(source, 0))
    source[5][0, 1] = 0.0
    with pytest.raises(ValueError, match='invented'):
        strong_ten_second_momentum_entry_mask(*source)


@pytest.mark.parametrize('field,clock', [(1, 40_000), (1, 20_000), (2, 10_000)])
def test_forming_stale_or_nonadjacent_source_is_rejected(field, clock):
    source = arrays([2.0], [1.0])
    source[field][0, 1] = clock
    with pytest.raises(ValueError, match='forming, stale or nonadjacent'):
        strong_ten_second_momentum_entry_mask(*source)


@pytest.mark.parametrize('dtype', [np.float32, np.int64, object])
def test_exact_float64_source_precision_is_required(dtype):
    source = arrays([2.0], [1.0])
    source[3] = source[3].astype(dtype)
    with pytest.raises(ValueError, match='aligned'):
        strong_ten_second_momentum_entry_mask(*source)


def test_finite_histogram_and_growth_threshold_overflows_fail_closed():
    maximum = np.finfo(np.float64).max
    source = arrays([maximum], [maximum])
    with pytest.raises(ValueError, match='overflows'):
        strong_ten_second_momentum_entry_mask(*source)
    with pytest.raises(ValueError, match='overflows'):
        strong_ten_second_momentum_entry(witness(source, 0))
    source[4][0, 1] = -maximum
    with pytest.raises(ValueError, match='overflows'):
        strong_ten_second_momentum_entry_mask(*source)


def test_native_scalar_prefix_consistency_and_input_immutability():
    source = arrays([0.1, 2.0, 1.0, -1.0], [0.0, 1.0, 1.0, -2.0])
    before = [value.copy() for value in source]
    full = strong_ten_second_momentum_entry_mask(*source)
    assert full.tolist() == [True, True, False, False]
    np.testing.assert_array_equal(full[:2], strong_ten_second_momentum_entry_mask(
        *(value[:2] for value in source)))
    for index in range(len(full)):
        assert bool(full[index]) is strong_ten_second_momentum_entry(witness(source, index))
    for value, original in zip(source, before):
        np.testing.assert_array_equal(value, original)
    assert strong_ten_second_momentum_entry_mask(*(value[:0] for value in source)).shape == (0,)


def test_scalar_witness_requires_exact_identity_and_finite_values():
    source = arrays([2.0], [1.0])
    value = witness(source, 0)
    for changed in (replace(value, ticker='aaa'), replace(value, source_build_id=str(uuid4())),
                    replace(value, observations=value.observations[::-1])):
        with pytest.raises(ValueError):
            strong_ten_second_momentum_entry(changed)
    with pytest.raises(ValueError):
        strong_ten_second_momentum_entry(object())


def test_policy_payload_is_explicit_and_detached():
    policy = strong_ten_second_momentum_policy_payload()
    assert policy['policy_id'] == POLICY_ID
    assert policy['fraction'] == HISTOGRAM_GROWTH_FRACTION == 0.10
    assert policy['resolution_ms'] == 10_000
    policy['fraction'] = 0.0
    assert strong_ten_second_momentum_policy_payload()['fraction'] == 0.10


def test_numbered_dispatch_preserves_thirteen_to_sixteen_and_strengthens_only_seventeen():
    from src.trading_runtime.strategy_rising_momentum_witness import numbered_momentum_entry
    weak = witness(arrays([1.0], [1.0]), 0)
    assert all(numbered_momentum_entry(weak, number) for number in (13, 14, 15, 16))
    assert not numbered_momentum_entry(weak, 17)
    assert numbered_momentum_entry(witness(arrays([2.0], [1.0]), 0), 17)


def test_typed_projector_and_restore_reject_weak_seventeen_evidence():
    from tests.test_strategy_one_intent import _proposal
    from src.trading_runtime.arte_rising_momentum_entry_v4 import (
        project_rising_momentum_entry, restore_rising_momentum,
    )
    weak = witness(arrays([1.0], [1.0]), 0)
    proposal = replace(_proposal(), boundary_ms=weak.boundary_ms,
                       strategy_number=17, momentum=weak)
    kwargs = dict(run_id='strong-momentum-test', batch_id=str(uuid4()),
                  parent_record_id=str(uuid4()), event_month='2026-08-01')
    with pytest.raises(ValueError, match='requires rising completed momentum'):
        project_rising_momentum_entry(proposal, **kwargs)
    rows = project_rising_momentum_entry(replace(proposal, strategy_number=14), **kwargs)
    assert restore_rising_momentum(rows, ticker='AAA', boundary_ms=weak.boundary_ms) == weak
    forged = tuple({**row, 'strategy_number': 17} for row in rows)
    with pytest.raises(ValueError, match='requires rising completed momentum'):
        restore_rising_momentum(forged, ticker='AAA', boundary_ms=weak.boundary_ms)
    strong = witness(arrays([2.0], [1.0]), 0)
    accepted = project_rising_momentum_entry(replace(proposal, momentum=strong), **kwargs)
    assert restore_rising_momentum(accepted, ticker='AAA', boundary_ms=strong.boundary_ms) == strong
