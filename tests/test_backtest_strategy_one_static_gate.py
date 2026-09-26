"""Certified scalar entry facts reduce to a vectorized necessary mask."""
from dataclasses import replace

import pytest

from src.backend.backtest_strategy_one_entry_store import (
    CertifiedEntryEvidencePlan,
)
from src.backend.backtest_strategy_one_static_gate import (
    MISSING_BOS_SUPPORT, MISSING_COMPLETED_BOS, MISSING_FROZEN_GAP,
    MISSING_INITIAL_PROTECTION, compile_static_entry_gate,
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
