"""A sealed declared release must reject independently changed capabilities."""
from dataclasses import replace
import json

import pytest

from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.numbered_fixed_strategy import numbered_fixed_strategy
from src.trading_runtime.strategy_sixty_six_contract import strategy_sixty_six_contract


def test_declared_factory_preserves_exact_parent_capabilities():
    child, parent = strategy_sixty_six_contract(), numbered_fixed_strategy(42)
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
    contract = strategy_sixty_six_contract()
    payload = json.loads(contract.policy_json)
    payload[policy][key] = value
    with pytest.raises(ValueError):
        replace(contract, policy_json=canonical_json(payload))


def test_recanonicalized_session_clock_cannot_change_sealed_session_rule():
    contract = strategy_sixty_six_contract()
    payload = json.loads(contract.policy_json)
    payload['session_policy']['windows'][0]['entry_cutoff'] = '10:00'
    with pytest.raises(ValueError):
        replace(contract, policy_json=canonical_json(payload))


def test_registered_policy_is_separate_and_old_spread_remains_mandatory():
    from src.trading_runtime.strategy_sixty_four_contract import strategy_sixty_four_contract
    child = numbered_fixed_strategy(66)
    assert child == strategy_sixty_six_contract()
    assert child.entry_spread_risk_policy is None
    assert child.all_held_original_risk_policy is not None
    old = strategy_sixty_four_contract()
    assert old.all_held_original_risk_policy is None
    payload = json.loads(old.policy_json)
    del payload['entry_spread_risk_policy']
    with pytest.raises(ValueError, match='spread rule requires'):
        replace(old, policy_json=canonical_json(payload))


@pytest.mark.parametrize('change', ['missing', 'foreign', 'fraction', 'boolean', 'unknown', 'half', 'missing_half'])
def test_all_held_adapter_rejects_changed_or_missing_declared_capability(change):
    contract = strategy_sixty_six_contract()
    payload = json.loads(contract.policy_json)
    policy = payload['all_held_original_risk_policy']
    if change == 'missing':
        del payload['all_held_original_risk_policy']
    elif change == 'foreign':
        policy['policy_id'] = 'foreign@1'
    elif change == 'fraction':
        policy['premarket_fraction'] = [1, 2]
    elif change == 'boolean':
        policy['quote_max_age_us'] = True
    elif change == 'unknown':
        payload['unknown_policy'] = {}
    elif change == 'missing_half':
        del payload['half_risk_liquidity_policy']
    else:
        payload['half_risk_liquidity_policy']['time_alone'] = 'always'
    with pytest.raises(ValueError):
        replace(contract, policy_json=canonical_json(payload))


def test_declared_rule_and_input_are_both_required():
    contract = strategy_sixty_six_contract()
    from src.trading_runtime.all_held_original_risk_failure import ALL_HELD_ORIGINAL_RISK_RULE, ALL_HELD_ORIGINAL_RISK_INPUT
    for field, removed in (('input_contracts', ALL_HELD_ORIGINAL_RISK_INPUT),
                           ('rule_set_contracts', ALL_HELD_ORIGINAL_RISK_RULE)):
        release = replace(contract.release, **{field: tuple(v for v in getattr(contract.release, field) if v != removed)}, approved_digest='')
        release = replace(release, approved_digest=release.digest())
        with pytest.raises(ValueError, match='exact rule, input'):
            replace(contract, release=release)


@pytest.mark.parametrize('factory', [strategy_sixty_six_contract])
def test_half_risk_companion_cannot_exist_without_its_declared_rule(factory):
    from src.trading_runtime.strategy_half_risk_liquidity_fade import POLICY_ID
    contract = factory()
    release = replace(contract.release, rule_set_contracts=tuple(
        v for v in contract.release.rule_set_contracts if v != POLICY_ID), approved_digest='')
    release = replace(release, approved_digest=release.digest())
    with pytest.raises(ValueError,match='exact companion'):
        replace(contract, release=release)
