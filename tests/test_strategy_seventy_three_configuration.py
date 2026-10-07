"""Prepared73 identity and immutable72 ancestry; synthetic external parent certificate only."""
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import ast
import pytest
from test_strategy_fifty_release import APPROVAL
from test_strategy_seventy_two_configuration import source_fixture as parent70_fixture
from src.backend.backtest_strategy_one_configuration import CertifiedStrategyOneConfiguration
from src.trading_runtime import strategy_seventy_three_release as child
from src.trading_runtime import strategy_seventy_two_release as parent
from src.trading_runtime.strategy_seventy_three_contract import strategy_seventy_three_contract
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.strategy_one_configuration_tree import encode_nodes,node_hash

def source_fixture():
    approval={**APPROVAL,'approved_code_commit':child.PARENT_CODE_COMMIT,'approved_code_fingerprint':child.PARENT_CODE_FINGERPRINT}
    result=parent.derive_strategy_seventy_two_configuration(parent70_fixture(),**approval)
    return CertifiedStrategyOneConfiguration(child.PARENT_REVISION_ID.split(':')[1],child.PARENT_PAYLOAD_HASH,result['node_hash'],result['source_candidate_id'],result['source_candidate_hash'],'prepared-test-only',result['payload'])
def result():return child.derive_strategy_seventy_three_configuration(source_fixture(),**APPROVAL)
def reseal(manifest):manifest['manifest_hash']=sha256(canonical_json({k:v for k,v in manifest.items() if k!='manifest_hash'}).encode()).hexdigest()
def test_exact_parent_installed_manifest_is_really_verified(monkeypatch):
    actual=parent.verify_strategy_seventy_two_manifest;calls=[]
    def verify(strategy):calls.append(strategy);return actual(strategy)
    monkeypatch.setattr(parent,'verify_strategy_seventy_two_manifest',verify)
    source=source_fixture();assert child.verify_exact_parent(source)==source.payload['strategy']['numbered_release']
    assert calls==[source.payload['strategy']]
def test_same_rules_inputs_policies_and_full_economics():
    source=source_fixture();before=deepcopy(source.payload);prepared=child.derive_strategy_seventy_three_configuration(source,**APPROVAL)
    old,new=parent.release_contract(),child.release_contract();new.verify()
    assert new.number==new.executor_revision==73 and new.input_contracts==old.input_contracts and new.rule_set_contracts==old.rule_set_contracts
    assert new.executor_strategy_id==old.executor_strategy_id and new.evaluation_interval==old.evaluation_interval
    assert child.CONFIRMED_ORIGINAL_RISK_POLICY==parent.CONFIRMED_ORIGINAL_RISK_POLICY
    assert child.INHERITED_POLICIES==parent.INHERITED_POLICIES and child.HALF_RISK_LIQUIDITY_POLICY==parent.HALF_RISK_LIQUIDITY_POLICY
    for key in before.keys()-{'strategy','strategy_profile','run_plan'}:assert prepared['payload'][key]==before[key]
    identity={'strategy_number','revision','name','profile_id','profile_revision','numbered_release'}
    for key in before['strategy'].keys()-identity:assert prepared['payload']['strategy'][key]==before['strategy'][key]
    assert source.payload==before
    assert prepared['payload_hash']==sha256(canonical_json(prepared['payload']).encode()).hexdigest()
    nodes=encode_nodes(prepared['payload']);assert prepared['node_hash']==node_hash(nodes) and prepared['node_count']==len(nodes)
    assert child.verify_prepared_strategy_seventy_three_manifest(prepared['payload']['strategy'])
def test_declared_contract_adapter_has_own_identity():
    new=strategy_seventy_three_contract();assert new.strategy_number==73 and new.release==child.release_contract()
    assert new.confirmed_original_risk_policy==parent.CONFIRMED_ORIGINAL_RISK_POLICY
@pytest.mark.parametrize('field,value',[('payload_hash','a'*64),('attempt_id','00000000-0000-0000-0000-000000000001')])
def test_foreign_certified_parent_rejected(field,value):
    with pytest.raises(ValueError):child.derive_strategy_seventy_three_configuration(replace(source_fixture(),**{field:value}),**APPROVAL)
@pytest.mark.parametrize('field,value',[('approved_code_commit','a'*40),('approved_code_fingerprint','a'*64)])
def test_resealed_parent_source_approval_drift_rejected(field,value):
    source=source_fixture();payload=deepcopy(source.payload);manifest=payload['strategy']['numbered_release'];manifest[field]=value;reseal(manifest)
    with pytest.raises(ValueError,match='parent source approval'):child.derive_strategy_seventy_three_configuration(replace(source,payload=payload),**APPROVAL)
@pytest.mark.parametrize('field,value',[('source_payload_hash','a'*64),('source_revision_id','foreign'),('publication_mode','live'),('approved_code_commit','bad'),('approved_code_fingerprint','bad')])
def test_resealed_own_authority_drift_rejected(field,value):
    strategy=result()['payload']['strategy'];manifest=strategy['numbered_release'];manifest[field]=value;reseal(manifest)
    with pytest.raises(ValueError):child.verify_prepared_strategy_seventy_three_manifest(strategy)
@pytest.mark.parametrize('defect',['policy','bool_alias','input_contract','extra_manifest','revision_alias'])
def test_complete_manifest_or_scalar_drift_rejected(defect):
    strategy=result()['payload']['strategy'];manifest=strategy['numbered_release']
    if defect=='policy':manifest['confirmed_original_risk_policy']['consecutive_buckets']=3
    elif defect=='bool_alias':manifest['confirmed_original_risk_policy']['original_risk_fraction'][0]=True
    elif defect=='input_contract':manifest['contract']['input_contracts']=[]
    elif defect=='extra_manifest':manifest['foreign']=True
    else:strategy['revision']=73.0
    reseal(manifest)
    with pytest.raises(ValueError):child.verify_prepared_strategy_seventy_three_manifest(strategy)
def test_compiler_uses_own_full_source_gate(monkeypatch):
    from pipelines.strategy_one.strategy_seventy_three_configuration import compile_strategy_seventy_three_configuration
    from src.backend import backtest_fixed_v4_certification as c
    calls=[];monkeypatch.setattr(c,'certify_numbered_fixed_v4_projection',lambda number:calls.append(number))
    envelope=compile_strategy_seventy_three_configuration(source_fixture(),**APPROVAL)
    assert calls==[73] and envelope['payload']['strategy']['strategy_number']==73
    # This is a source-gate fixture seam only; root owns actual73 certification integration.
def test_publisher_selects_exact_parent72_and_own73_receiver():
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    tree=ast.parse((root/'scripts/clickhouse/publish_strategy_seventy_three_configuration.py').read_text())
    calls=[n for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='certify_numbered_configuration']
    assert len(calls)==1 and ast.literal_eval(calls[0].args[1])==72
    text=ast.unparse(tree)
    assert 'certify_numbered_fixed_v4_projection(73)' in text
    assert 'publish_strategy_seventy_three_configuration.py --receive-stdin' in text

def test_actual_receiver_envelope_acceptance_before_publication():
    from pipelines.strategy_one.configuration_publisher import _verified_numbered_envelope
    prepared = result()
    payload, nodes = _verified_numbered_envelope(prepared)
    assert payload == prepared['payload']
    assert nodes == encode_nodes(payload)
    assert prepared['source_candidate_id'] == f'strategy-seventy-three-from:{child.PARENT_REVISION_ID}'
    assert prepared['source_candidate_hash'] == child.PARENT_PAYLOAD_HASH

@pytest.mark.parametrize('prefix', ['strategy-seventy-from', 'strategy-seventy-two-from', 'strategy-seventy-three-other'])
def test_actual_receiver_rejects_wrong_envelope_provenance(prefix):
    from pipelines.strategy_one.configuration_publisher import _verified_numbered_envelope
    prepared = result()
    prepared['source_candidate_id'] = f'{prefix}:{child.PARENT_REVISION_ID}'
    with pytest.raises(ValueError, match='Numbered source provenance differs'):
        _verified_numbered_envelope(prepared)


def test_actual_receiver_rejects_foreign_source_hash():
    from pipelines.strategy_one.configuration_publisher import _verified_numbered_envelope
    prepared = result()
    prepared['source_candidate_hash'] = 'a' * 64
    with pytest.raises(ValueError, match='Numbered source provenance differs'):
        _verified_numbered_envelope(prepared)