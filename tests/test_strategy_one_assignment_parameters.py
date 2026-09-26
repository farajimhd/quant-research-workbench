"""Numbered Strategy 1 inputs must not resolve through the legacy executor."""
from __future__ import annotations

import pytest

from src.backend.trading_configuration_service import merged_assignment_parameters
from src.trading_runtime.strategy_one_contract import STRATEGY_ID


def test_strategy_one_uses_sealed_tick_not_oms_default():
    config = {"strategy": {"strategy_id": STRATEGY_ID, "strategy_number": 1,
                           "revision": 1, "parameters": {
                               "execution": {"tick_size": 0.05},
                               "sizing": {"max_risk": 1}}},
              "oms": {"settings": {"tick_size": 0.01}}}
    resolved = merged_assignment_parameters(config, {"parameters": {}})
    assert resolved == {"execution": {"tick_size": 0.05},
                        "sizing": {"max_risk": 1}}
    with pytest.raises(ValueError, match="sealed inputs"):
        merged_assignment_parameters(config, {"parameters": {
            "execution": {"tick_size": 0.01}}})
