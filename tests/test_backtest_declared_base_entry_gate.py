"""Exact declared base reduction parity; no native financial approval."""
from copy import deepcopy
from dataclasses import replace
import json

import numpy as np
import pytest

from test_declared_native_fixed_capabilities import prepared
from test_backtest_strategy_one_static_gate import _entry
from test_backtest_strategy_one_entry_store import _plans
from src.backend.backtest_declared_base_entry_gate import (
    DeclaredBaseEntryPolicy, compile_declared_base_entry_gate,
)
from src.backend.backtest_strategy_one_static_gate import compile_static_entry_gate
from src.trading_runtime.journal_contract import canonical_json


def plans_for(boundaries, starts, breaks):
    plans = _plans()
    entry = _entry(plans)
    row = plans[1].prepared[0]
    count = len(boundaries)
    changed = replace(row, source_rows=count, row_index=np.arange(count),
                      boundary_ms=np.array(boundaries, dtype=np.int64),
                      episode_start_ms=np.array(starts, dtype=np.int64),
                      macd_boundary_ms=np.tile([31_000] * 4, (count, 1)),
                      stop_bar_boundary_ms=np.full(count, 30_000),
                      stop_low_int=np.full(count, 99_000))
    candidates = replace(plans[1], prepared=(changed,))
    facts = tuple(replace(entry.candidates[0], boundary_ms=at,
                          episode_start_ms=start, bos_break_boundary_ms=bos)
                  for at, start, bos in zip(boundaries, starts, breaks, strict=True))
    activations = tuple(replace(entry.activations[0], episode_start_ms=start)
                        for start in dict.fromkeys(starts))
    return candidates, replace(entry, candidates=facts, activations=activations)


@pytest.mark.parametrize('boundary,start', [
    (1100, 0), (1100, 1100),
    (19_499_900, 19_499_000), (19_500_000, 19_499_000),
    (43_200_100, 43_199_900), (43_200_100, 43_200_000),
    (43_200_100, 43_200_100), (43_200_100, 43_200_200),
    (56_999_900, 56_999_000), (57_000_000, 56_999_000),
])
def test_declared_window_boundaries_equal_existing_base_kernel(prepared, boundary, start):
    capabilities = prepared[0]
    candidates, entry = plans_for([boundary], [start], [boundary // 1000 * 1000])
    expected = compile_static_entry_gate(candidates, entry, strategy_number=12)
    actual = compile_declared_base_entry_gate(candidates, entry, capabilities=capabilities)
    assert actual.facts == expected.facts
    assert np.array_equal(actual.rejection_mask, expected.rejection_mask)
    assert np.array_equal(actual.eligible_indices, expected.eligible_indices)


def test_declared_recent_boundary_prefix_and_future_tail_preserve_source_order(prepared):
    capabilities = prepared[0]
    candidates, entry = plans_for([31_000, 31_100, 61_000], [30_000] * 3, [1000] * 3)
    actual = compile_declared_base_entry_gate(candidates, entry, capabilities=capabilities)
    old = compile_static_entry_gate(candidates, entry, strategy_number=12)
    assert np.array_equal(actual.rejection_mask, old.rejection_mask)
    prefix_row = replace(candidates.prepared[0], boundary_ms=candidates.prepared[0].boundary_ms[:1],
                         episode_start_ms=candidates.prepared[0].episode_start_ms[:1])
    prefix = compile_declared_base_entry_gate(replace(candidates, prepared=(prefix_row,)),
                                             entry, capabilities=capabilities)
    assert np.array_equal(prefix.rejection_mask, actual.rejection_mask[:1])
    changed_tail = replace(entry, candidates=(*entry.candidates[:2],
                           replace(entry.candidates[2], protection_valid=False)))
    tail = compile_declared_base_entry_gate(candidates, changed_tail, capabilities=capabilities)
    assert np.array_equal(tail.rejection_mask[:2], actual.rejection_mask[:2])
    with pytest.raises(ValueError):
        actual.rejection_mask[0] = 1
    with pytest.raises(ValueError):
        actual.eligible_indices[0] = 1


def test_all_independent_missing_bits_preserve_base_parity(prepared):
    candidates, entry = plans_for([31_000], [30_000], [None])
    fact = replace(entry.candidates[0], bos_support_kind='', bos_support_level_id='',
                   protection_valid=False)
    entry = replace(entry, candidates=(fact,),
                    activations=(replace(entry.activations[0], average_gap=None),))
    old = compile_static_entry_gate(candidates, entry, strategy_number=12)
    actual = compile_declared_base_entry_gate(candidates, entry, capabilities=prepared[0])
    assert np.array_equal(actual.rejection_mask, old.rejection_mask)
    assert not actual.eligible_indices.size


@pytest.mark.parametrize('break_clock', [32_000, 1001, -1000, True, 1000.0])
def test_bad_source_break_is_integrity_failure(prepared, break_clock):
    candidates, entry = plans_for([31_000], [30_000], [break_clock])
    with pytest.raises(ValueError, match='source clocks|exact integers'):
        compile_declared_base_entry_gate(candidates, entry, capabilities=prepared[0])


@pytest.mark.parametrize('mutation', ['episode', 'activation_duplicate', 'build', 'cardinality', 'session'])
def test_source_identity_drift_rejects_before_mask(prepared, mutation):
    candidates, entry = plans_for([31_000], [30_000], [1000])
    if mutation == 'episode':
        entry = replace(entry, candidates=(replace(entry.candidates[0], episode_start_ms=29_900),))
    elif mutation == 'activation_duplicate':
        entry = replace(entry, activations=entry.activations * 2)
    elif mutation == 'build':
        entry = replace(entry, source_build_id='foreign')
    elif mutation == 'session':
        entry = replace(entry, session_date='2026-08-19')
    else:
        candidates = replace(candidates, prepared=(replace(candidates.prepared[0],
                             episode_start_ms=np.array([], dtype=np.int64)),))
    with pytest.raises(ValueError):
        compile_declared_base_entry_gate(candidates, entry, capabilities=prepared[0])


@pytest.mark.parametrize('concern', ['session_policy', 'activation_policy', 'recent_bos_policy'])
def test_changed_inherited_rule_rejects_under_old_contract(prepared, concern):
    payload = deepcopy(prepared[0].payload()['inherited'])
    payload['policies'][concern]['unknown'] = 'not a supported revision'
    changed = replace(prepared[0], inherited_json=canonical_json(payload))
    with pytest.raises(ValueError, match='unsupported inherited'):
        DeclaredBaseEntryPolicy(changed)


def test_numeric_coercion_cannot_select_changed_policy(prepared):
    payload = deepcopy(prepared[0].payload()['inherited'])
    payload['policies']['recent_bos_policy']['maximum_bos_age_ms'] = 30_000.0
    changed = replace(prepared[0], inherited_json=canonical_json(payload))
    with pytest.raises(ValueError, match='unsupported inherited'):
        DeclaredBaseEntryPolicy(changed)


def test_new_own_identity_cannot_change_rule_mask(prepared):
    caps = prepared[0]
    other = replace(caps, identity=replace(caps.identity, strategy_number=9999, revision=9999))
    candidates, entry = plans_for([31_000], [30_000], [1000])
    a = compile_declared_base_entry_gate(candidates, entry, capabilities=caps)
    b = compile_declared_base_entry_gate(candidates, entry, capabilities=other)
    assert np.array_equal(a.rejection_mask, b.rejection_mask)


@pytest.mark.parametrize("field", ["boundary_ms", "episode_start_ms"])
@pytest.mark.parametrize("values", [np.array([31000.5]), np.array([True]),
                                    [31000], np.array([[31000]], dtype=np.int64)])
def test_candidate_clock_columns_reject_coercion(prepared, field, values):
    candidates, entry = plans_for([31_000], [30_000], [1000])
    row = replace(candidates.prepared[0], **{field: values})
    with pytest.raises(ValueError, match="exact Int64 clocks"):
        compile_declared_base_entry_gate(replace(candidates, prepared=(row,)),
                                         entry, capabilities=prepared[0])
