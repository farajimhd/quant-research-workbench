"""Real candidate controls and declared price-risk behavior, without publication."""
from dataclasses import replace
import pytest

from src.trading_runtime import strategy_fifty_seven_release as parent
from src.trading_runtime.strategy_one_hundred_thirteen_release import (
    release_contract, declared_policies, PRICE_POLICY,
)
from src.trading_runtime.strategy_one_hundred_thirteen_contract import strategy_one_hundred_thirteen_contract
from src.trading_runtime.price_confirmed_original_risk import (
    INPUT, RULE, POLICY_KEY, price_confirmed_original_risk_failure,
)
from test_price_confirmed_original_risk import value


def test_candidate_retains_exact_control_policy_values_and_returns_owned_copies():
    policies = declared_policies()
    assert {k: policies[k] for k in parent.INHERITED_POLICIES} == parent.INHERITED_POLICIES
    assert policies['early_original_risk_failure_policy'] == parent.EARLY_FAILURE_POLICY_PAYLOAD
    assert policies['entry_spread_risk_policy'] == parent.ENTRY_SPREAD_RISK_POLICY_PAYLOAD
    assert policies['half_risk_liquidity_policy'] == parent.HALF_RISK_LIQUIDITY_POLICY
    policies[POLICY_KEY]['eligibility_ms'] = 1
    assert declared_policies()[POLICY_KEY]['eligibility_ms'] == 120000
    release = release_contract()
    assert release == release_contract()
    assert release.input_contracts.count(INPUT) == release.rule_set_contracts.count(RULE) == 1
    assert release.evaluation_interval == parent.release_contract().evaluation_interval
    with pytest.raises(ValueError):
        replace(release, behavior_specification='foreign behavior').verify()


@pytest.mark.parametrize('boundary,first', [(10000, 100), (43210000, 43200100)])
def test_actual_candidate_selects_half_risk_rule_for_both_sessions(boundary, first):
    contract = strategy_one_hundred_thirteen_contract()
    assert contract.price_confirmed_original_risk_policy == PRICE_POLICY
    assert contract.early_original_risk_policy is not None
    assert contract.allows_adds is False
    assert contract.allows_completed_30s_trailing is False
    observed = value(boundary_ms=boundary, first_held_boundary_ms=first,
        completed_five_second_boundary_ms=boundary)
    assert price_confirmed_original_risk_failure(observed, policy=contract.price_confirmed_original_risk_policy)
    assert price_confirmed_original_risk_failure(replace(observed, bid=9.5001),
        policy=contract.price_confirmed_original_risk_policy) is None
