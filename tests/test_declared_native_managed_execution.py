from copy import deepcopy
from dataclasses import replace

import pytest

from test_declared_native_execution import prepared, execution_case, seal, APPROVAL
from src.trading_runtime import declared_native_managed_execution as module
from src.trading_runtime.declared_native_management_request import inherited_fixed_management_request_policy
from src.trading_runtime.declared_native_execution import prepare_declared_execution_configuration
from test_backtest_declared_native_fixed_assignments import setup


@pytest.fixture
def managed_case(execution_case):
    parent, execution = execution_case
    return parent, module.DeclaredNativeManagedExecutionSpec(execution, inherited_fixed_management_request_policy())


def test_full_management_seal_preserves_complete_parent_economics(managed_case):
    parent, spec = managed_case
    before = prepare_declared_execution_configuration(parent[2], spec.execution, approval=APPROVAL)
    envelope = module.prepare_declared_managed_configuration(parent[2], spec, approval=APPROVAL)
    release = module.verify_declared_managed_configuration(parent[2], spec, envelope, approval=APPROVAL)
    assert spec.management_request.policy_id in release.input_contracts
    assert module.INPUT_CONTRACT in release.input_contracts
    assert module.declared_managed_spec_from_configuration(envelope['payload']) == spec
    old, new = deepcopy(before['payload']), deepcopy(envelope['payload'])
    for value in (old, new):
        value['strategy'].pop('numbered_release')
        value['strategy_profile'].pop('description')
    assert old == new


@pytest.mark.parametrize('change', ['cash', 'fees', 'run', 'request', 'nested_request',
    'permissions', 'source', 'contract', 'identity_alias', 'count_alias', 'approval'])
def test_independent_recompilation_rejects_resealed_drift(managed_case, change):
    parent, spec = managed_case
    envelope = module.prepare_declared_managed_configuration(parent[2], spec, approval=APPROVAL)
    strategy = envelope['payload']['strategy']
    manifest = strategy['numbered_release']
    if change == 'cash': envelope['payload']['private_cash'] = 100000
    elif change == 'fees': envelope['payload']['private_free_fills'] = True
    elif change == 'run': envelope['payload']['run_plan']['private_scope'] = 'probe'
    elif change == 'request': del manifest['declared_native_management_request']
    elif change == 'nested_request': manifest['native_fixed_managed_execution']['management_request']['exit']['time_in_force'] = ''
    elif change == 'permissions': manifest['declared_native_assignment_source']['permissions']['reenter'] = False
    elif change == 'source': manifest['declared_native_entry_source']['quote_product'] = 'foreign'
    elif change == 'contract': manifest['contract']['input_contracts'].remove(spec.management_request.policy_id)
    elif change == 'identity_alias': strategy['revision'] = float(strategy['revision'])
    elif change == 'approval': manifest['approved_code_fingerprint'] = 'f'*64
    seal(envelope)
    if change == 'count_alias': envelope['node_count'] = float(envelope['node_count'])
    with pytest.raises(ValueError):
        module.verify_declared_managed_configuration(parent[2], spec, envelope, approval=APPROVAL)


@pytest.mark.parametrize('field', ['native_fixed_execution', 'native_fixed_candidate',
    'declared_native_entry_source', 'declared_native_assignment_source',
    'declared_native_entry_request', 'declared_native_management_request'])
def test_extraction_rejects_each_missing_duplicate(managed_case, field):
    parent, spec = managed_case
    envelope = module.prepare_declared_managed_configuration(parent[2], spec, approval=APPROVAL)
    del envelope['payload']['strategy']['numbered_release'][field]
    seal(envelope)
    with pytest.raises(ValueError): module.declared_managed_spec_from_configuration(envelope['payload'])


def test_management_policy_is_mandatory(managed_case):
    _, spec = managed_case
    with pytest.raises(ValueError): replace(spec, management_request=None)
    value = spec.payload()
    del value['management_request']
    with pytest.raises(ValueError): module.parse_declared_native_managed_execution(value)


def test_managed_dated_assignments_use_actual_full_configuration_hash_and_same_source_order(setup):
    from src.backend.backtest_declared_native_fixed_assignments import (
        prepare_declared_assignment_plan, prepare_declared_managed_assignment_plan,
    )
    old = prepare_declared_assignment_plan(setup.client, **setup.args)
    spec = module.DeclaredNativeManagedExecutionSpec(setup.args['spec'], inherited_fixed_management_request_policy())
    envelope = module.prepare_declared_managed_configuration(setup.client, spec, approval=APPROVAL)
    setup.native['configuration_hash'] = envelope['payload_hash']
    args = dict(setup.args, spec=spec, envelope=envelope)
    actual = prepare_declared_managed_assignment_plan(setup.client, **args)
    assert actual.configuration_hash == envelope['payload_hash'] != old.configuration_hash
    assert actual.scopes == old.scopes
    assert actual.account_bindings == old.account_bindings
    assert actual.candidate_token == old.candidate_token
    assert actual.identity_token == old.identity_token
    assert actual.market_token == old.market_token
    assert actual.token != old.token
    with pytest.raises(ValueError): prepare_declared_assignment_plan(setup.client, **args)


@pytest.mark.parametrize('change', ['declared_request', 'native_hash', 'core_spec_alias'])
def test_managed_assignment_loader_cannot_use_partially_verified_configuration(setup, change):
    from src.backend.backtest_declared_native_fixed_assignments import prepare_declared_managed_assignment_plan
    spec = module.DeclaredNativeManagedExecutionSpec(setup.args['spec'], inherited_fixed_management_request_policy())
    envelope = module.prepare_declared_managed_configuration(setup.client, spec, approval=APPROVAL)
    setup.native['configuration_hash'] = envelope['payload_hash']
    if change == 'declared_request':
        envelope['payload']['strategy']['numbered_release']['declared_native_management_request']['exit']['time_in_force'] = ''
        seal(envelope)
    elif change == 'native_hash': setup.native['configuration_hash'] = 'f'*64
    else: spec = spec.execution
    with pytest.raises(ValueError):
        prepare_declared_managed_assignment_plan(setup.client,
            **dict(setup.args, spec=spec, envelope=envelope))
