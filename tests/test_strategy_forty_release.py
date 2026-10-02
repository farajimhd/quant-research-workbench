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


def test_installed_release_requires_complete_parent_and_registered_source_proof():
    from pipelines.strategy_one.strategy_forty_configuration import compile_strategy_forty_configuration
    from src.backend.backtest_fixed_v4_certification import certify_numbered_fixed_v4_projection
    from src.trading_runtime.strategy_registry import numbered_strategy,numbered_strategy_parent
    result=compile_strategy_forty_configuration(source_fixture(),**APPROVAL)
    assert child.verify_strategy_forty_manifest(result['payload']['strategy'])
    assert numbered_strategy(40)==child.release_contract() and numbered_strategy_parent(40)==39
    assert len(certify_numbered_fixed_v4_projection(40))==64
    with pytest.raises(ValueError):numbered_strategy(41)


@pytest.mark.parametrize('relative',[
    'src/trading_runtime/strategy_forty_release.py',
    'pipelines/strategy_one/strategy_forty_configuration.py',
    'src/trading_runtime/strategy_registry.py',
    'src/trading_runtime/numbered_fixed_strategy.py',
    'src/backend/backtest_strategy_one_configuration.py',
    'pipelines/strategy_one/configuration_publisher.py',
    'scripts/clickhouse/publish_strategy_forty_configuration.py',
])
def test_certificate_rejects_unreviewed_registration_and_source(relative,tmp_path):
    from pathlib import Path
    from src.backend.backtest_strategy_forty_certification import certify_strategy_forty_source
    changed=tmp_path/'changed.py'
    changed.write_text(Path(relative).read_text(encoding='utf-8')+'\nUNREVIEWED_CHANGE=True\n',encoding='utf-8')
    with pytest.raises(ValueError,match='pinned release source changed'):
        certify_strategy_forty_source(source_overrides={relative:changed})


def test_installed_capabilities_preserve_parent_session_and_order_policy():
    from src.trading_runtime.numbered_fixed_strategy import numbered_fixed_strategy
    previous,current=numbered_fixed_strategy(39),numbered_fixed_strategy(40)
    for name in ('allows_session_exit','allows_adds','allows_completed_30s_trailing',
                 'allows_target_escalation','caps_entry_at_reference_ask','allows_followthrough_failure_exit'):
        assert getattr(current,name)==getattr(previous,name)
    for boundary in (0,100,19_499_900,19_500_000,19_740_000,19_800_000,
                     43_200_000,43_200_100,57_000_000,57_300_000,57_600_000):
        for name in ('entry_allowed','acquisition_cutoff','liquidation_due'):
            assert getattr(current,name)(boundary)==getattr(previous,name)(boundary)
        for episode in (0,100,19_500_000,43_200_000,43_200_100):
            assert current.activation_allowed(boundary,episode)==previous.activation_allowed(boundary,episode)
