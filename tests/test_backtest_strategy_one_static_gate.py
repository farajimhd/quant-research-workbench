"""Certified scalar entry facts reduce to a vectorized necessary mask."""
from dataclasses import replace

import numpy as np
import pytest

from src.backend.backtest_strategy_one_entry_store import (
    CertifiedEntryEvidencePlan,
)
from src.backend.backtest_strategy_one_static_gate import (
    MISSING_BOS_SUPPORT, MISSING_COMPLETED_BOS, MISSING_FROZEN_GAP,
    MISSING_INITIAL_PROTECTION, StrategyOneStaticGate,
    compile_static_entry_gate, project_static_survivors,
)
from test_backtest_strategy_one_entry_store import Reader, _plans


def _entry(plans):
    reader = Reader(plans)
    return CertifiedEntryEvidencePlan(
        plans[0].build_id, plans[0].sessions[0], (),
        (reader.activation,), (reader.candidate,), "e" * 64)


def test_static_gate_compiles_eligible_prefix_without_market_query():
    plans = _plans()
    compiled = compile_static_entry_gate(plans[1], _entry(plans))
    assert len(compiled.facts) == 1
    assert compiled.rejection_mask.tolist() == [0]
    assert compiled.eligible_indices.tolist() == [0]
    with pytest.raises(ValueError):
        compiled.rejection_mask[0] = 1


def test_static_gate_reports_independent_rejection_bits():
    plans = _plans()
    entry = _entry(plans)
    candidate = replace(entry.candidates[0], bos_break_boundary_ms=None,
                        bos_pivot_id="", bos_break_close_int=None,
                        bos_support_kind="", bos_support_level_id="",
                        bos_support_pivot_id="", protection_valid=False,
                        stop_price=None, target_price=None,
                        target_level_id="", target_ordinal=None)
    changed = replace(
        entry, activations=(replace(entry.activations[0], average_gap=None),),
        candidates=(candidate,))
    result = compile_static_entry_gate(plans[1], changed)
    assert result.eligible_indices.size == 0
    assert result.rejection_mask.tolist() == [
        MISSING_FROZEN_GAP | MISSING_COMPLETED_BOS |
        MISSING_BOS_SUPPORT | MISSING_INITIAL_PROTECTION]


def test_static_gate_rejects_episode_drift_not_as_a_normal_rejection():
    plans = _plans()
    entry = _entry(plans)
    altered = replace(entry, candidates=(replace(
        entry.candidates[0], episode_start_ms=29_900),))
    with pytest.raises(ValueError, match="changed candidate episode"):
        compile_static_entry_gate(plans[1], altered)


def test_static_survivors_remove_unneeded_market_reads_not_parent_seals():
    plans = _plans()
    row = plans[1].prepared[0]
    extended = replace(
        row, source_rows=2, row_index=np.array([0, 1]),
        boundary_ms=np.array([31_000, 31_100]),
        episode_start_ms=np.array([30_000, 30_000]),
        macd_boundary_ms=np.array([[31_000] * 4, [31_000] * 4]),
        stop_bar_boundary_ms=np.array([30_000, 30_000]),
        stop_low_int=np.array([99_000, 99_000]))
    candidates = replace(plans[1], prepared=(extended,))
    first = Reader(plans).candidate
    gate = StrategyOneStaticGate(
        (first, replace(first, boundary_ms=31_100)),
        np.array([MISSING_FROZEN_GAP, 0], dtype=np.uint8),
        np.array([1], dtype=np.int64))
    visible, activations = project_static_survivors(
        candidates, plans[2], gate)
    assert visible.coverage is candidates.coverage
    assert visible.prepared[0].boundary_ms.tolist() == [31_100]
    assert visible.prepared[0].row_index.tolist() == [1]
    assert activations.rows == plans[2].rows
    assert activations is plans[2]
    assert visible.token != candidates.token
    repeated, repeated_activations = project_static_survivors(
        candidates, plans[2], gate)
    assert (repeated.token, repeated_activations.token) == (
        visible.token, activations.token)


def test_static_survivors_reject_cross_plan_or_missing_activation():
    plans = _plans()
    entry = _entry(plans)
    gate = compile_static_entry_gate(plans[1], entry)
    with pytest.raises(ValueError, match="differs"):
        project_static_survivors(plans[1], replace(plans[2], rows=()), gate)
    with pytest.raises(ValueError, match="differs"):
        project_static_survivors(plans[1], plans[2], replace(
            gate, facts=(replace(gate.facts[0], boundary_ms=31_100),)))


def test_static_survivors_keep_rejected_episode_activation_for_active_state():
    plans = _plans()
    row = plans[1].prepared[0]
    extended = replace(
        row, source_rows=2, row_index=np.array([0, 1]),
        boundary_ms=np.array([31_000, 41_000]),
        episode_start_ms=np.array([30_000, 40_000]),
        macd_boundary_ms=np.array([[31_000] * 4, [41_000] * 4]),
        stop_bar_boundary_ms=np.array([30_000, 30_000]),
        stop_low_int=np.array([99_000, 99_000]))
    candidates = replace(plans[1], prepared=(extended,))
    first = Reader(plans).candidate
    gate = StrategyOneStaticGate(
        (first, replace(first, boundary_ms=41_000, episode_start_ms=40_000)),
        np.array([0, MISSING_INITIAL_PROTECTION], dtype=np.uint8),
        np.array([0], dtype=np.int64))
    all_activations = replace(plans[2], rows=(
        plans[2].rows[0], replace(plans[2].rows[0], boundary_ms=40_000)))
    pruned, preserved = project_static_survivors(
        candidates, all_activations, gate)
    assert pruned.prepared[0].boundary_ms.tolist() == [31_000]
    assert preserved is all_activations
    assert [row.boundary_ms for row in preserved.rows] == [30_000, 40_000]
