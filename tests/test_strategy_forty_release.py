"""Prepared precision-only successor preserves every Strategy39 trading rule."""
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import pytest
from src.backend.backtest_strategy_one_configuration import CertifiedStrategyOneConfiguration
from src.trading_runtime import strategy_thirty_nine_release as parent
from src.trading_runtime import strategy_forty_release as child
from src.trading_runtime.journal_contract import canonical_json
from test_strategy_thirty_nine_release import source_fixture as strategy38_fixture
from test_strategy_thirty_three_configuration import APPROVAL


def source_fixture():
    result=parent.derive_strategy_thirty_nine_configuration(strategy38_fixture(),**APPROVAL)
    return CertifiedStrategyOneConfiguration(child.PARENT_REVISION_ID.split(':')[1],
        child.PARENT_PAYLOAD_HASH,result['node_hash'],result['source_candidate_id'],
        result['source_candidate_hash'],'test-only',result['payload'])


def test_precision_successor_preserves_complete_trading_payload_and_rule_contracts():
    source=source_fixture(); before=deepcopy(source.payload)
    result=child.derive_strategy_forty_configuration(source,**APPROVAL)
    assert source.payload==before
    previous,current=parent.release_contract(),child.release_contract()
    current.verify()
    assert current.number==current.executor_revision==40
    assert current.rule_set_contracts==previous.rule_set_contracts
    assert current.input_contracts==previous.input_contracts
    assert current.evaluation_interval==previous.evaluation_interval
    assert child.INHERITED_POLICIES==parent.INHERITED_POLICIES
    assert child.HALF_RISK_LIQUIDITY_POLICY==parent.HALF_RISK_LIQUIDITY_POLICY
    for name in before.keys()-{'strategy','strategy_profile','run_plan'}:
        assert result['payload'][name]==before[name]
    identities={'strategy_number','revision','name','profile_id','profile_revision','numbered_release'}
    for name in before['strategy'].keys()-identities:
        assert result['payload']['strategy'][name]==before['strategy'][name]
    assert result['payload_hash']==sha256(canonical_json(result['payload']).encode()).hexdigest()
    assert result['source_candidate_id']=='strategy-forty-from:'+child.PARENT_REVISION_ID
    assert child.verify_prepared_strategy_forty_manifest(result['payload']['strategy'])


@pytest.mark.parametrize('field,value',[
    ('payload_hash','a'*64),('attempt_id','00000000-0000-0000-0000-000000000001'),
])
def test_prepared_successor_rejects_foreign_exact_parent(field,value):
    with pytest.raises(ValueError):child.derive_strategy_forty_configuration(replace(source_fixture(),**{field:value}),**APPROVAL)


@pytest.mark.parametrize('policy',['half_risk_liquidity_policy','episode_activity_policy','liquidity_fade_policy'])
def test_resealed_policy_mutation_cannot_change_trading_rule(policy):
    strategy=deepcopy(child.derive_strategy_forty_configuration(source_fixture(),**APPROVAL)['payload']['strategy'])
    manifest=strategy['numbered_release'];manifest[policy]['unreviewed_change']=True
    manifest['manifest_hash']=sha256(canonical_json({k:v for k,v in manifest.items() if k!='manifest_hash'}).encode()).hexdigest()
    with pytest.raises(ValueError,match='pinned policy'):child.verify_prepared_strategy_forty_manifest(strategy)


def test_prepared_specification_does_not_admit_execution_before_installation():
    strategy=child.derive_strategy_forty_configuration(source_fixture(),**APPROVAL)['payload']['strategy']
    assert child.verify_prepared_strategy_forty_manifest(strategy)
    with pytest.raises(ValueError):child.verify_strategy_forty_manifest(strategy)
