"""Performance changes must preserve causal decisions and checkpoint values."""
from copy import deepcopy
from dataclasses import replace
import json
from unittest.mock import patch
from uuid import UUID

import pytest

from src.market_engine.immutable_evidence import FrozenDict, freeze
from tests.test_early_squeeze_momentum import M, S, advance, momentum_fixture


def test_parameter_cache_shares_equal_settings_and_detects_nested_edits():
    h, a, trade, _, _ = momentum_fixture()
    h._momentum_parameters_cache.clear()
    with patch.object(S, 'resolve_long_momentum_parameters', wraps=S.resolve_long_momentum_parameters) as resolve:
        for _ in range(5):
            h.evaluate(replace(a, parameters=deepcopy(a.parameters)), trade(16.01))
        assert resolve.call_count == 1
        changed = deepcopy(a.parameters)
        changed.setdefault('execution', {})['tick_size'] = .02
        h.evaluate(replace(a, parameters=changed), trade(16.01))
        assert resolve.call_count == 2
        changed['execution']['tick_size'] = .03
        h.evaluate(replace(a, parameters=changed), trade(16.01))
        assert resolve.call_count == 3
        h.evaluate(a, trade(16.01))
        assert resolve.call_count == 3


def test_parameter_cache_is_bounded():
    h, a, trade, _, _ = momentum_fixture()
    for n in range(20):
        p = deepcopy(a.parameters)
        p.setdefault('execution', {})['tick_size'] = .01+n*.001
        h.evaluate(replace(a, parameters=p), trade(16.01))
    assert len(h._momentum_parameters_cache) == 8


def test_geometry_shares_identity_but_computation_remains_detached():
    h, a, trade, one, _ = momentum_fixture()
    before = json.dumps(a.state, sort_keys=True)
    result = h.evaluate(a, trade(16.02, 10.44))
    assert json.dumps(a.state, sort_keys=True) == before
    a = advance(a, result)
    book = a.state['squeeze_breakout']['resistance_1s']
    gap = a.state['squeeze_breakout']['frozen_gap']
    assert isinstance(book['levels'], FrozenDict)
    assert isinstance(gap, FrozenDict)
    assert deepcopy(gap) is gap
    with pytest.raises(TypeError):
        gap['average'] = 123
    before = json.dumps(a.state, sort_keys=True)
    result = h.evaluate(a, one(17, 10.6, .2, .1))
    assert json.dumps(a.state, sort_keys=True) == before
    assert result.state['squeeze_breakout']['frozen_gap'] is gap
    assert result.state['squeeze_breakout']['resistance_1s'] is not book


@pytest.mark.parametrize('held', [False, True])
def test_plain_checkpoint_and_sealed_state_produce_identical_results(held):
    h, a, trade, one, fast = momentum_fixture()
    if held:
        a = advance(a, h.evaluate(a, trade(16.02, 10.44)))
        a = replace(a, status=S.AssignmentStatus.MANAGING)
    restored = replace(a, state=json.loads(json.dumps(a.state)))
    observations = [one(17, 10.6, .2, .1), fast(17.005, 10.6), trade(17.01, 10.6, 100 if held else 0)]
    with patch.object(S, 'uuid4', return_value=UUID(int=1)):
        for observation in observations:
            left = h.evaluate(a, observation)
            right = h.evaluate(restored, observation)
            assert left.status == right.status
            assert left.state == right.state
            assert left.evaluation == right.evaluation
            a, restored = advance(a, left), advance(restored, right)
            # Model JSON checkpoint restore at every causal boundary.
            restored = replace(restored, state=json.loads(json.dumps(restored.state)))
