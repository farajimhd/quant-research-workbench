"""Complete configuration readback, not installed or financial qualification.

The existing fixture reads real normalized parent nodes/derivation. Parent
native code certification is a named seam here while the new runtime channel
is under source review; these tests do not approve that source change.
"""
from copy import deepcopy
from dataclasses import replace, FrozenInstanceError
from hashlib import sha256

import pytest

from test_declared_native_fixed_capabilities import prepared
from test_declared_native_fixed_candidate import candidate, APPROVAL
from src.trading_runtime import declared_native_execution as module
from src.trading_runtime import declared_native_fixed_candidate as core
from src.trading_runtime.declared_native_entry_source import DeclaredNativeEntrySourcePolicy, INPUT_CONTRACT
from src.trading_runtime.declared_native_fixed_capabilities import _json
from src.backend.backtest_declared_native_fixed_assignments import DeclaredAssignmentPolicy
from src.trading_runtime.declared_native_entry_request import inherited_fixed_entry_request_policy
from src.trading_runtime.strategy_one_configuration_tree import encode_nodes, node_hash


@pytest.fixture
def execution_case(prepared, monkeypatch):
    import src.backend.backtest_fixed_v4_certification as certifier
    monkeypatch.setattr(certifier, 'certify_numbered_fixed_v4_projection', lambda _: 'c'*64)
    spec = module.DeclaredNativeExecutionSpec(candidate(prepared),
        DeclaredNativeEntrySourcePolicy(core.QUOTE_SOURCE_CONTRACT), DeclaredAssignmentPolicy(),
        inherited_fixed_entry_request_policy())
    return prepared, spec


def seal(envelope):
    manifest=envelope['payload']['strategy']['numbered_release']
    manifest['manifest_hash']=sha256(_json({k:v for k,v in manifest.items() if k!='manifest_hash'}).encode()).hexdigest()
    envelope['nodes']=list(encode_nodes(envelope['payload']))
    envelope['node_count']=len(envelope['nodes']);envelope['node_hash']=node_hash(envelope['nodes'])
    envelope['payload_hash']=sha256(_json(envelope['payload']).encode()).hexdigest()


def test_full_execution_declares_sources_without_a_spread_delta(execution_case):
    prepared,spec=execution_case
    envelope=module.prepare_declared_execution_configuration(prepared[2],spec,approval=APPROVAL)
    release=module.verify_declared_execution_configuration(prepared[2],spec,envelope,approval=APPROVAL)
    manifest=envelope['payload']['strategy']['numbered_release']
    assert spec.candidate.delta.spread is None
    assert INPUT_CONTRACT in release.input_contracts
    assert core.QUOTE_SOURCE_CONTRACT in release.input_contracts
    assert release.input_contracts.count(core.QUOTE_SOURCE_CONTRACT)==1
    assert spec.assignments.policy_id in release.input_contracts
    assert manifest['declared_native_entry_source']==spec.entry_source.payload()
    assert manifest['declared_native_assignment_source']==spec.assignments.payload()
    assert manifest['declared_native_entry_request']==spec.entry_request.payload()
    assert spec.entry_request.policy_id in release.input_contracts
    assert module.declared_execution_spec_from_configuration(envelope['payload'])==spec
    assert all(query.startswith('SELECT ') for query in prepared[2].queries)
    with pytest.raises(FrozenInstanceError):spec.assignments=None
    value=spec.payload();value['assignments']['permissions']['add']=False
    assert spec.assignments.payload()['permissions']['add'] is True


def test_combined_candidate_uses_the_same_explicit_source_contract(execution_case):
    prepared,spec=execution_case
    if prepared[3].strategy_number==57:
        return  # Its quarter-spread policy is already inherited, never replaced.
    spec=replace(spec,candidate=candidate(prepared,True))
    envelope=module.prepare_declared_execution_configuration(prepared[2],spec,approval=APPROVAL)
    module.verify_declared_execution_configuration(prepared[2],spec,envelope,approval=APPROVAL)
    assert spec.release().input_contracts.count(core.QUOTE_SOURCE_CONTRACT)==1
    assert envelope['payload']['strategy']['numbered_release']['entry_spread_risk_policy']==spec.candidate.delta.spread.payload()


def test_only_declared_manifest_changes_extend_original_candidate(execution_case):
    prepared,spec=execution_case
    core_envelope=core.prepare_candidate_configuration(prepared[2],spec.candidate,approval=APPROVAL)
    full=module.prepare_declared_execution_configuration(prepared[2],spec,approval=APPROVAL)
    restored=deepcopy(full['payload'])
    restored['strategy']['numbered_release']=deepcopy(core_envelope['payload']['strategy']['numbered_release'])
    restored['strategy_profile']['description']=core_envelope['payload']['strategy_profile']['description']
    assert restored==core_envelope['payload']
    assert module.parse_declared_native_execution(spec.payload())==spec
    assert core.parse_candidate_spec(spec.candidate.payload())==spec.candidate
    assert core_envelope['source_candidate_hash']==full['source_candidate_hash']


@pytest.mark.parametrize('change',['missing','unknown','bool_version','old_version',
    'permissions','identity_rule','parameter_rule','source','freshness','candidate_alias','entry_capital'])
def test_execution_schema_requires_complete_exact_source_rules(execution_case,change):
    _,spec=execution_case;value=spec.payload()
    if change=='missing':del value['entry_source']
    elif change=='unknown':value['private_selector']=True
    elif change=='bool_version':value['schema_version']=True
    elif change=='old_version':value['schema_version']=2
    elif change=='permissions':value['assignments']['permissions']['add']=1
    elif change=='identity_rule':value['assignments']['identity_rule']='foreign'
    elif change=='parameter_rule':value['assignments']['parameter_rule']='partial_parameters'
    elif change=='source':value['entry_source']['quote_source']['predicate']='bucket_only'
    elif change=='freshness':value['entry_source']['freshness']='future_quote'
    elif change=='entry_capital':value['entry_request']['capital_request']['fraction']=[1,2]
    else:value['candidate']['base']['identity']['revision']=float(value['candidate']['base']['identity']['revision'])
    with pytest.raises(ValueError):module.parse_declared_native_execution(value)


@pytest.mark.parametrize('change',['economics','fees','run','missing_source','permissions',
    'duplicate_source','contract','approval','lineage','count_alias','unknown_envelope','request'])
def test_independent_full_recompilation_rejects_caller_resealed_changes(execution_case,change):
    prepared,spec=execution_case
    envelope=module.prepare_declared_execution_configuration(prepared[2],spec,approval=APPROVAL)
    manifest=envelope['payload']['strategy']['numbered_release']
    if change=='economics':envelope['payload']['strategy']['parameters']['execution']['tick_size']=999
    elif change=='fees':envelope['payload']['private_zero_cost']=True
    elif change=='run':envelope['payload']['run_plan']['private_scope']='small_probe'
    elif change=='missing_source':del manifest['declared_native_entry_source']
    elif change=='permissions':manifest['declared_native_assignment_source']['permissions']['reenter']=False
    elif change=='duplicate_source':manifest['native_fixed_execution']['entry_source']['source_population']='proposal_only'
    elif change=='contract':manifest['contract']['input_contracts'].remove(INPUT_CONTRACT)
    elif change=='approval':manifest['approved_code_fingerprint']='f'*64
    elif change=='lineage':envelope['source_candidate_hash']='f'*64
    elif change=='unknown_envelope':envelope['private_override']=True
    elif change=='request':manifest['declared_native_entry_request']['capital_request']['fraction']=[1,2]
    seal(envelope)
    if change=='count_alias':envelope['node_count']=float(envelope['node_count'])
    with pytest.raises(ValueError):
        module.verify_declared_execution_configuration(prepared[2],spec,envelope,approval=APPROVAL)


@pytest.mark.parametrize('change',['own_revision','source','candidate','assignment','contract','request'])
def test_manifest_extraction_checks_every_duplicate_source_and_identity(execution_case,change):
    prepared,spec=execution_case
    envelope=module.prepare_declared_execution_configuration(prepared[2],spec,approval=APPROVAL)
    manifest=envelope['payload']['strategy']['numbered_release']
    if change=='own_revision':envelope['payload']['strategy']['revision']=float(spec.candidate.base.identity.revision)
    elif change=='source':manifest['declared_native_entry_source']['quote_product']='foreign'
    elif change=='candidate':manifest['native_fixed_candidate']['labels']['name']='foreign'
    elif change=='assignment':manifest['declared_native_assignment_source']['permissions']['exit']=False
    elif change=='request':manifest['declared_native_entry_request']['requested_quantity']=1.0
    else:manifest['contract']['input_contracts'].remove(INPUT_CONTRACT)
    seal(envelope)
    with pytest.raises(ValueError):module.declared_execution_spec_from_configuration(envelope['payload'])


def test_preparation_requires_actual_parent_source_proof(execution_case,monkeypatch):
    prepared,spec=execution_case
    import src.backend.backtest_fixed_v4_certification as certifier
    def denied(_):raise RuntimeError('parent source mismatch')
    monkeypatch.setattr(certifier,'certify_numbered_fixed_v4_projection',denied)
    with pytest.raises(RuntimeError,match='parent source mismatch'):
        module.prepare_declared_execution_configuration(prepared[2],spec,approval=APPROVAL)


@pytest.mark.parametrize('field',['candidate','entry_source','assignments','entry_request'])
def test_no_freeflag_execution_activation(execution_case,field):
    _,spec=execution_case
    with pytest.raises(ValueError):replace(spec,**{field:True})
