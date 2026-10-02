from dataclasses import replace

import numpy as np
import pytest

from test_backtest_strategy_entry_activity_source import source_authority, ActivityBars
from src.backend.backtest_strategy_entry_activity_source import load_entry_activity_plan
from src.backend.backtest_strategy_entry_activity_gate import ENTRY_ACTIVITY_FADED, ENTRY_ACTIVITY_MISSING
from src.backend.backtest_strategy_episode_activity_gate import (
    EPISODE_ACTIVITY_VETOED, compile_episode_activity_static_gate,
)


def test_certified_fade_blocks_later_recovery_with_original_episode_preserved():
    market, parent = source_authority()
    activity = load_entry_activity_plan(market, parent, client=ActivityBars('fade'))
    gate = compile_episode_activity_static_gate(activity)
    assert gate.rejection_mask.tolist() == [ENTRY_ACTIVITY_FADED | EPISODE_ACTIVITY_VETOED,
                                          EPISODE_ACTIVITY_VETOED]
    assert not len(gate.eligible_indices)
    assert [fact.episode_start_ms for fact in gate.facts] == [30000, 30000]
    with pytest.raises(ValueError):
        gate.rejection_mask.setflags(write=True)
    with pytest.raises(ValueError, match='causal reduction'):
        replace(gate, token='0' * 64)
    with pytest.raises(ValueError, match='causal reduction'):
        replace(gate, rejection_mask=np.zeros(2, dtype=np.uint16))


def test_missing_native_observations_never_latch_episode_failure():
    market, parent = source_authority()
    activity = load_entry_activity_plan(market, parent, client=ActivityBars('missing'))
    gate = compile_episode_activity_static_gate(activity)
    assert gate.rejection_mask.tolist() == [ENTRY_ACTIVITY_MISSING] * 2


def test_prepared_gate_requires_exact_certified_source():
    with pytest.raises(ValueError, match='exact certified'):
        compile_episode_activity_static_gate(object())
