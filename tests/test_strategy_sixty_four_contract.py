"""A sealed declared release must reject independently changed capabilities."""
from dataclasses import replace
import json

import pytest

from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.numbered_fixed_strategy import numbered_fixed_strategy
from src.trading_runtime.strategy_sixty_four_contract import strategy_sixty_four_contract


def test_declared_factory_preserves_exact_parent_capabilities():
    child, parent = strategy_sixty_four_contract(), numbered_fixed_strategy(42)
    for key in ('allows_session_exit', 'allows_adds', 'allows_completed_30s_trailing',
                'allows_target_escalation', 'caps_entry_at_reference_ask',
                'allows_followthrough_failure_exit', 'early_original_risk_policy'):
        assert getattr(child, key) == getattr(parent, key)
    for clock in (0, 100, 19_499_900, 19_500_000, 19_740_000, 19_800_000,
                  43_200_000, 43_200_100, 57_000_000, 57_300_000, 57_600_000):
        for method in ('entry_allowed', 'acquisition_cutoff', 'liquidation_due'):
            assert getattr(child, method)(clock) == getattr(parent, method)(clock)
        for episode_start in (0, 100, 19_499_900, 19_500_000, 43_200_000,
                              43_200_100, 57_000_000, clock + 100):
            assert child.activation_allowed(clock, episode_start) == parent.activation_allowed(clock, episode_start)


@pytest.mark.parametrize('policy,key,value', [
    ('add_policy', 'allows_adds', True),
    ('trailing_policy', 'completed_30s_low_trailing', True),
    ('target_policy', 'target_escalation', True),
    ('entry_price_policy', 'maximum_buy_price', 'unbounded'),
])
def test_recanonicalized_policy_cannot_change_sealed_capabilities(policy, key, value):
    contract = strategy_sixty_four_contract()
    payload = json.loads(contract.policy_json)
    payload[policy][key] = value
    with pytest.raises(ValueError):
        replace(contract, policy_json=canonical_json(payload))


def test_recanonicalized_session_clock_cannot_change_sealed_session_rule():
    contract = strategy_sixty_four_contract()
    payload = json.loads(contract.policy_json)
    payload['session_policy']['windows'][0]['entry_cutoff'] = '10:00'
    with pytest.raises(ValueError):
        replace(contract, policy_json=canonical_json(payload))
