"""Prepared76 identity and immutable75 ancestry; synthetic external parent certificate only."""
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import ast
import pytest
from test_strategy_fifty_release import APPROVAL
from test_strategy_seventy_five_configuration import source_fixture as parent_source_fixture
from src.backend.backtest_strategy_one_configuration import CertifiedStrategyOneConfiguration
from src.trading_runtime import strategy_seventy_six_release as child
from src.trading_runtime import strategy_seventy_five_release as parent
from src.trading_runtime.strategy_seventy_six_contract import strategy_seventy_six_contract
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.strategy_one_configuration_tree import encode_nodes,node_hash

def source_fixture():
    approval={**APPROVAL,'approved_code_commit':child.PARENT_CODE_COMMIT,'approved_code_fingerprint':child.PARENT_CODE_FINGERPRINT}
    result=parent.derive_strategy_seventy_five_configuration(parent_source_fixture(),**approval)
    return CertifiedStrategyOneConfiguration(child.PARENT_REVISION_ID.split(':')[1],child.PARENT_PAYLOAD_HASH,result['node_hash'],result['source_candidate_id'],result['source_candidate_hash'],'prepared-test-only',result['payload'])
def result():return child.derive_strategy_seventy_six_configuration(source_fixture(),**APPROVAL)
def reseal(manifest):manifest['manifest_hash']=sha256(canonical_json({k:v for k,v in manifest.items() if k!='manifest_hash'}).encode()).hexdigest()
def test_exact_parent_installed_manifest_is_really_verified(monkeypatch):
    actual=parent.verify_strategy_seventy_five_manifest;calls=[]
    def verify(strategy):calls.append(strategy);return actual(strategy)
    monkeypatch.setattr(parent,'verify_strategy_seventy_five_manifest',verify)
    source=source_fixture();assert child.verify_exact_parent(source)==source.payload['strategy']['numbered_release']
    assert calls==[source.payload['strategy']]
def test_same_rules_inputs_policies_and_full_economics():
    source=source_fixture();before=deepcopy(source.payload);prepared=child.derive_strategy_seventy_six_configuration(source,**APPROVAL)
    old,new=parent.release_contract(),child.release_contract();new.verify()
    assert new.number==new.executor_revision==76 and new.input_contracts==old.input_contracts and new.rule_set_contracts==old.rule_set_contracts
    assert new.executor_strategy_id==old.executor_strategy_id and new.evaluation_interval==old.evaluation_interval
    assert child.CONFIRMED_ORIGINAL_RISK_POLICY==parent.CONFIRMED_ORIGINAL_RISK_POLICY
    assert child.INHERITED_POLICIES==parent.INHERITED_POLICIES and child.HALF_RISK_LIQUIDITY_POLICY==parent.HALF_RISK_LIQUIDITY_POLICY
    for key in before.keys()-{'strategy','strategy_profile','run_plan'}:assert prepared['payload'][key]==before[key]
    identity={'strategy_number','revision','name','profile_id','profile_revision','numbered_release'}
    for key in before['strategy'].keys()-identity:assert prepared['payload']['strategy'][key]==before['strategy'][key]
    assert source.payload==before
    assert prepared['payload_hash']==sha256(canonical_json(prepared['payload']).encode()).hexdigest()
    nodes=encode_nodes(prepared['payload']);assert prepared['node_hash']==node_hash(nodes) and prepared['node_count']==len(nodes)
    assert child.verify_prepared_strategy_seventy_six_manifest(prepared['payload']['strategy'])
def test_declared_contract_adapter_has_own_identity():
    new=strategy_seventy_six_contract();assert new.strategy_number==76 and new.release==child.release_contract()
    assert new.confirmed_original_risk_policy==parent.CONFIRMED_ORIGINAL_RISK_POLICY
@pytest.mark.parametrize('field,value',[('payload_hash','a'*64),('attempt_id','00000000-0000-0000-0000-000000000001')])
def test_foreign_certified_parent_rejected(field,value):
    with pytest.raises(ValueError):child.derive_strategy_seventy_six_configuration(replace(source_fixture(),**{field:value}),**APPROVAL)
@pytest.mark.parametrize('field,value',[('approved_code_commit','a'*40),('approved_code_fingerprint','a'*64)])
def test_resealed_parent_source_approval_drift_rejected(field,value):
    source=source_fixture();payload=deepcopy(source.payload);manifest=payload['strategy']['numbered_release'];manifest[field]=value;reseal(manifest)
    with pytest.raises(ValueError,match='parent source approval'):child.derive_strategy_seventy_six_configuration(replace(source,payload=payload),**APPROVAL)
@pytest.mark.parametrize('field,value',[('source_payload_hash','a'*64),('source_revision_id','foreign'),('publication_mode','live'),('approved_code_commit','bad'),('approved_code_fingerprint','bad')])
def test_resealed_own_authority_drift_rejected(field,value):
    strategy=result()['payload']['strategy'];manifest=strategy['numbered_release'];manifest[field]=value;reseal(manifest)
    with pytest.raises(ValueError):child.verify_prepared_strategy_seventy_six_manifest(strategy)
@pytest.mark.parametrize('defect',['policy','bool_alias','input_contract','extra_manifest','revision_alias'])
def test_complete_manifest_or_scalar_drift_rejected(defect):
    strategy=result()['payload']['strategy'];manifest=strategy['numbered_release']
    if defect=='policy':manifest['confirmed_original_risk_policy']['consecutive_buckets']=3
    elif defect=='bool_alias':manifest['confirmed_original_risk_policy']['original_risk_fraction'][0]=True
    elif defect=='input_contract':manifest['contract']['input_contracts']=[]
    elif defect=='extra_manifest':manifest['foreign']=True
    else:strategy['revision']=76.0
    reseal(manifest)
    with pytest.raises(ValueError):child.verify_prepared_strategy_seventy_six_manifest(strategy)
def test_compiler_uses_own_full_source_gate(monkeypatch):
    from pipelines.strategy_one.strategy_seventy_six_configuration import compile_strategy_seventy_six_configuration
    from src.backend import backtest_fixed_v4_certification as c
    calls=[];monkeypatch.setattr(c,'certify_numbered_fixed_v4_projection',lambda number:calls.append(number))
    envelope=compile_strategy_seventy_six_configuration(source_fixture(),**APPROVAL)
    assert calls==[76] and envelope['payload']['strategy']['strategy_number']==76
    # This is a source-gate fixture seam only; root owns actual75 certification integration.
def test_publisher_selects_exact_parent72_and_own75_receiver():
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    tree=ast.parse((root/'scripts/clickhouse/publish_strategy_seventy_six_configuration.py').read_text())
    calls=[n for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='certify_numbered_configuration']
    assert len(calls)==1 and ast.literal_eval(calls[0].args[1])==75
    text=ast.unparse(tree)
    assert 'certify_numbered_fixed_v4_projection(76)' in text
    assert 'publish_strategy_seventy_six_configuration.py --receive-stdin' in text

def test_actual_receiver_envelope_acceptance_before_publication():
    from pipelines.strategy_one.configuration_publisher import _verified_numbered_envelope
    prepared = result()
    payload, nodes = _verified_numbered_envelope(prepared)
    assert payload == prepared['payload']
    assert nodes == encode_nodes(payload)
    assert prepared['source_candidate_id'] == f'strategy-seventy-six-from:{child.PARENT_REVISION_ID}'
    assert prepared['source_candidate_hash'] == child.PARENT_PAYLOAD_HASH

@pytest.mark.parametrize('prefix', ['strategy-seventy-from', 'strategy-seventy-two-from', 'strategy-seventy-six-other'])
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

def test_official_publisher_resolves_own_compiler_before_legacy_fallback():
    from pathlib import Path
    path=Path(__file__).parents[1]/'pipelines/strategy_one/configuration_publisher.py'
    tree=ast.parse(path.read_text(encoding='utf-8'))
    function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='publish_configuration')
    branches=[n for n in ast.walk(function) if isinstance(n,ast.If) and ast.unparse(n.test)=='number == 76']
    assert len(branches)==1 and len(branches[0].body)==1
    selected=branches[0].body[0]
    assert isinstance(selected,ast.ImportFrom)
    assert selected.module=='pipelines.strategy_one.strategy_seventy_six_configuration'
    assert [(n.name,n.asname) for n in selected.names]==[('compile_strategy_seventy_six_configuration','compile_configuration')]


def test_actual_publisher_compiles_own_full_native_configuration_before_database_boundary(monkeypatch):
    from pipelines.strategy_one import configuration_publisher as publisher
    from pipelines.strategy_one import strategy_seventy_six_configuration as compiler
    from pipelines.strategy_one import strategy_forty_nine_configuration as legacy_fallback
    source=source_fixture(); prepared=result(); compiled=[]; parent_reads=[]
    actual_compile=compiler.compile_strategy_seventy_six_configuration
    def parent_certificate(client,number):
        # Explicit external normalized parent-read boundary only.
        parent_reads.append(number)
        assert number==75
        return source
    def compile_selected(source_value,**approval):
        value=actual_compile(source_value,**approval)
        compiled.append(value)
        return value
    class DatabaseBoundaryReached(RuntimeError): pass
    def database_layout_boundary(client):
        assert compiled==[prepared]
        raise DatabaseBoundaryReached('No database operation allowed by this component test')
    def forbidden_fallback(*args,**kwargs):
        raise AssertionError('Strategy76 must not compile the legacy49 fallback')
    monkeypatch.setattr(publisher,'certify_numbered_configuration',parent_certificate)
    monkeypatch.setattr(compiler,'compile_strategy_seventy_six_configuration',compile_selected)
    monkeypatch.setattr(legacy_fallback,'compile_strategy_forty_nine_configuration',forbidden_fallback)
    monkeypatch.setattr(publisher,'verify_tables',database_layout_boundary)
    with pytest.raises(publisher.PublicationStageError) as caught:
        publisher.publish_configuration(object(),object(),prepared)
    assert isinstance(caught.value.__cause__,DatabaseBoundaryReached)
    assert parent_reads==[75] and len(compiled)==1
    assert compiled[0]['payload']['strategy']['strategy_number']==76
    assert compiled[0]['source_candidate_id']==f'strategy-seventy-six-from:{child.PARENT_REVISION_ID}'
    assert compiled[0]['source_candidate_hash']==source.payload_hash
    assert compiled[0]['payload']['strategy']['numbered_release']['confirmed_original_risk_policy']==parent.CONFIRMED_ORIGINAL_RISK_POLICY_PAYLOAD
    for key in source.payload.keys()-{'strategy','strategy_profile','run_plan'}:
        assert compiled[0]['payload'][key]==source.payload[key]


def test_parent_source_commit_is_exact_published75_not_its_parent73():
    assert child.PARENT_CODE_COMMIT == 'd66a2f151d9128145ed9ca2ea8dd07914b6120c2'
    assert child.PARENT_CODE_FINGERPRINT == 'd4794eceba721508c2fc1e18a11fc68fb65d0f0c45a9b25c4e653b49cfb04484'


def test_actual_saved_published75_full_payload_and_all_parent_pins():
    """Saved official SELECT certificate evidence; not a fresh DB acceptance."""
    import json
    from pathlib import Path
    path=Path('D:/TradingML/runtimes/strategy-optimization-20261005/strategy75-certified-published-identity-v1.json')
    if not path.is_file():
        pytest.skip('Pinned official development75 normalized identity receipt unavailable')
    assert sha256(path.read_bytes()).hexdigest()=='f34e6ad322cbd8eb74ff44904f6ad4735600e206006ab6bd7ece1f0caa1becd0'
    saved=json.loads(path.read_text(encoding='utf-8'));payload=saved['revision']['payload']
    assert saved['revision']['revision_id']=='strategy-one-75:aec64642-b1df-4fae-b380-a1a952d46998'
    assert saved['payload_hash']=='82b8b803d0f250528996cabf7225b3beb5a5bc4c0b237aa93cd686c496224b16'
    assert sha256(canonical_json(payload).encode()).hexdigest()==saved['payload_hash']
    nodes=encode_nodes(payload);assert node_hash(nodes)==saved['node_hash']
    manifest=payload['strategy']['numbered_release']
    assert manifest['approved_code_commit']=='d66a2f151d9128145ed9ca2ea8dd07914b6120c2'
    assert manifest['approved_code_fingerprint']=='d4794eceba721508c2fc1e18a11fc68fb65d0f0c45a9b25c4e653b49cfb04484'
    assert manifest['approved_digest']=='dd9de51c00eeaa8b99e47679f83692ad5d36f52d1e468438edb9ff292ad9ac6e'
    assert parent.release_contract().canonical_payload()==manifest['contract']
    assert parent.release_contract().approved_digest==manifest['approved_digest']
    for name,value in {**parent.INHERITED_POLICIES,'half_risk_liquidity_policy':parent.HALF_RISK_LIQUIDITY_POLICY,'confirmed_original_risk_policy':parent.CONFIRMED_ORIGINAL_RISK_POLICY_PAYLOAD}.items():
        assert canonical_json(manifest[name])==canonical_json(value)
    source=CertifiedStrategyOneConfiguration(saved['release_attempt_id'],saved['payload_hash'],saved['node_hash'],
        'strategy-seventy-five-from:'+manifest['source_revision_id'],manifest['source_payload_hash'],saved['token'],payload)
    assert child.verify_exact_parent(source)==manifest
    prepared=child.derive_strategy_seventy_six_configuration(source,**APPROVAL)
    for name in payload.keys()-{'strategy','strategy_profile','run_plan'}:
        assert canonical_json(prepared['payload'][name])==canonical_json(payload[name])
    assert prepared['source_candidate_hash']==saved['payload_hash']
    assert prepared['source_candidate_id']=='strategy-seventy-six-from:'+saved['revision']['revision_id']
