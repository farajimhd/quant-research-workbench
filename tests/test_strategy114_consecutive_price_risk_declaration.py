from dataclasses import replace

import pytest

from src.trading_runtime import strategy_one_hundred_thirteen_release as parent
from src.trading_runtime.strategy_one_hundred_fourteen_release import release_contract, declared_policies, PRICE_POLICY
from src.trading_runtime.strategy_one_hundred_fourteen_contract import strategy_one_hundred_fourteen_contract
from src.trading_runtime.consecutive_price_confirmed_risk import INPUT, RULE, POLICY_KEY
from src.trading_runtime.price_confirmed_original_risk import INPUT as SINGLE_INPUT, RULE as SINGLE_RULE, POLICY_KEY as SINGLE_KEY


def test_only_the_single_candle_extension_is_replaced_and_parent_is_immutable():
    before = parent.release_contract()
    old = parent.declared_policies()
    new = release_contract()
    policies = declared_policies()
    assert new.number == new.executor_revision == 114
    assert new.evaluation_interval == before.evaluation_interval == '100ms'
    assert set(new.input_contracts)-{INPUT} == set(before.input_contracts)-{SINGLE_INPUT}
    assert set(new.rule_set_contracts)-{RULE} == set(before.rule_set_contracts)-{SINGLE_RULE}
    assert new.input_contracts.count(INPUT) == new.rule_set_contracts.count(RULE) == 1
    assert {k: v for k, v in policies.items() if k != POLICY_KEY} == {
        k: v for k, v in old.items() if k != SINGLE_KEY}
    assert parent.release_contract() == before and parent.declared_policies() == old
    policies[POLICY_KEY]['price_policy']['eligibility_ms'] = 1
    assert declared_policies()[POLICY_KEY]['price_policy']['eligibility_ms'] == 120000


def test_actual_candidate_retains_controls_and_owns_both_session_confirmation_rules():
    contract = strategy_one_hundred_fourteen_contract()
    assert contract.confirmed_original_risk_policy == PRICE_POLICY
    assert contract.price_confirmed_original_risk_policy is None
    assert PRICE_POLICY.price_policy.premarket_fraction == (1, 2)
    assert PRICE_POLICY.price_policy.afterhours_fraction == (1, 2)
    assert contract.entry_spread_risk_policy is not None
    assert contract.early_original_risk_policy is not None
    assert contract.allows_adds is False
    assert contract.allows_completed_30s_trailing is False
    assert contract.allows_session_exit and contract.allows_followthrough_failure_exit


def test_sealed_behavior_cannot_be_rewritten_under_the_same_digest():
    with pytest.raises(ValueError):
        replace(release_contract(), behavior_specification='foreign').verify()
