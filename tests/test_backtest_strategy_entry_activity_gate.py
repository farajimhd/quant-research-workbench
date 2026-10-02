from dataclasses import replace

import numpy as np
import pytest

from test_backtest_strategy_entry_activity_source import source_authority, ActivityBars
from test_backtest_strategy_rising_momentum import sources
from src.backend.backtest_strategy_entry_activity_source import load_entry_activity_plan
from src.backend.backtest_strategy_entry_activity_gate import (
    ENTRY_ACTIVITY_FADED, ENTRY_ACTIVITY_MISSING, compile_entry_activity_static_gate,
)
from src.backend.backtest_strategy_certified_price_break import compile_certified_price_static_gate
from src.backend.backtest_strategy_one_static_gate import project_static_survivors


def test_reduction_preserves_original_activation_and_parent_gate():
    market, parent = source_authority()
    original = compile_certified_price_static_gate(parent)
    plan = load_entry_activity_plan(market, parent, client=ActivityBars('fade'))
    gate = compile_entry_activity_static_gate(plan)
    assert gate.rejection_mask.dtype == np.uint16
    assert gate.rejection_mask.tolist() == [ENTRY_ACTIVITY_FADED, 0]
    assert original.rejection_mask.dtype == np.uint8
    assert original.rejection_mask.tolist() == [0, 0]
    activations = sources((31000, 41000))[2]
    survivors, retained = project_static_survivors(parent.candidates, activations, gate)
    assert survivors.prepared[0].boundary_ms.tolist() == [41000]
    assert survivors.prepared[0].episode_start_ms.tolist() == [30000]
    assert retained is activations
    assert gate.eligible_indices.tolist() == [1]
    with pytest.raises(ValueError):
        gate.rejection_mask.setflags(write=True)
    with pytest.raises(ValueError, match='reduction'):
        replace(gate, rejection_mask=np.asarray([0, 0], dtype=np.uint16))


def test_missing_and_decline_have_separate_reasons():
    market, parent = source_authority()
    plan = load_entry_activity_plan(market, parent, client=ActivityBars('missing'))
    gate = compile_entry_activity_static_gate(plan)
    assert gate.rejection_mask.tolist() == [ENTRY_ACTIVITY_MISSING, ENTRY_ACTIVITY_MISSING]
    assert not len(gate.eligible_indices)


def test_gate_rejects_unbound_source():
    with pytest.raises(ValueError, match='exact certified'):
        compile_entry_activity_static_gate(object())
