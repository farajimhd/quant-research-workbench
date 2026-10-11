from src.trading_runtime import strategy_one_hundred_seventeen_release as parent
from src.trading_runtime.strategy_one_hundred_eighteen_release import (
    release_contract,declared_policies,CHANNEL_POLICY,
)
from src.trading_runtime.strategy_one_hundred_eighteen_contract import strategy_one_hundred_eighteen_contract
from src.trading_runtime.native_channel_management_policy import RULE,SOURCE_CONTRACT,POLICY_KEY


def test_channel_successor_preserves_every_inherited_policy_and_contract():
    prior=parent.release_contract()
    release=release_contract()
    assert release.number==release.executor_revision==118
    assert release.rule_set_contracts==prior.rule_set_contracts+(RULE,)
    assert release.input_contracts==prior.input_contracts+(SOURCE_CONTRACT,)
    assert release.executor_strategy_id==prior.executor_strategy_id
    assert release.evaluation_interval==prior.evaluation_interval
    policies=declared_policies()
    assert {key:value for key,value in policies.items() if key!=POLICY_KEY}==parent.declared_policies()
    contract=strategy_one_hundred_eighteen_contract()
    assert contract.native_channel_management_policy==CHANNEL_POLICY
    assert contract.confirmed_original_risk_policy is not None
