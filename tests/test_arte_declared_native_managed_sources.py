"""Full child config plus real producer reload; native authority seams explicit.

The shared setup uses normalized parent nodes and physical Arrow/quote reads.
Its fenced run and product certificates are fixtures; no durable historical
finance or installed strategy approval follows from these component tests.
"""
from copy import deepcopy
from dataclasses import replace
import json

import pytest

from test_arte_declared_native_fixed_sources import setup, install_completed_reader
from test_declared_native_execution import seal, APPROVAL
from src.backend.backtest_declared_native_fixed_assignments import DeclaredAssignmentPolicy
from src.trading_runtime.arte_declared_native_fixed_sources import PreparedDeclaredSourceResolver
from src.trading_runtime.arte_declared_native_managed_sources import PreparedDeclaredManagedSourceResolver
from src.trading_runtime.declared_native_execution import DeclaredNativeExecutionSpec
from src.trading_runtime.declared_native_entry_request import inherited_fixed_entry_request_policy
from src.trading_runtime.declared_native_management_request import inherited_fixed_management_request_policy
from src.trading_runtime.declared_native_managed_execution import (
    DeclaredNativeManagedExecutionSpec, prepare_declared_managed_configuration,
)


@pytest.fixture
def managed(setup):
    x = setup
    x.old_hash = x.envelope['payload_hash']
    x.managed_spec = DeclaredNativeManagedExecutionSpec(
        DeclaredNativeExecutionSpec(x.spec, x.resolver.source_policy,
            DeclaredAssignmentPolicy(), inherited_fixed_entry_request_policy()),
        inherited_fixed_management_request_policy())
    x.managed_envelope = prepare_declared_managed_configuration(x.client, x.managed_spec, approval=APPROVAL)
    x.context['configuration_hash'] = x.managed_envelope['payload_hash']
    x.managed_resolver = PreparedDeclaredManagedSourceResolver(x.client, run_id=x.context['run_id'],
        spec=x.managed_spec, envelope=x.managed_envelope, approval=APPROVAL, market=x.market)
    return x


def test_full_child_hash_is_preserved_through_actual_entry_reload(managed):
    x = managed
    value = x.managed_resolver.reconstruct_entry_facts(x.proposal)
    assert value.proposal == x.proposal
    assert value.configuration_hash == x.managed_envelope['payload_hash'] != x.old_hash
    assert x.managed_resolver.compare_entry_witnesses(x.proposal, value) == value
    assert json.loads(x.managed_resolver._envelope) == x.managed_envelope
    assert set(x.calls) == {'market', 'candidates', 'activations', 'seeds', 'pivots', 'hod', 'entry'}
    assert any('attempt_id=toUUID' in q for q in x.source.queries)
    with pytest.raises(ValueError):
        PreparedDeclaredSourceResolver(x.client, run_id=x.context['run_id'], candidate=x.spec,
            envelope=x.managed_envelope, approval=APPROVAL, market=x.market,
            source_policy=x.managed_spec.execution.entry_source)


def test_completed_management_facts_use_full_child_hash(managed):
    x = managed
    install_completed_reader(x)
    value = x.managed_resolver.completed_producer_facts(x.proposal.ticker, x.proposal.boundary_ms)
    assert value.configuration_hash == x.managed_envelope['payload_hash']
    assert x.managed_resolver.compare_completed_facts(value) == value


@pytest.mark.parametrize('change', ['management', 'entry', 'assignments', 'fees', 'cash', 'count_alias', 'candidate_only'])
def test_resealed_child_drift_fails_before_producer_queries(managed, change):
    x = managed
    envelope = deepcopy(x.managed_envelope)
    manifest = envelope['payload']['strategy']['numbered_release']
    if change == 'management': manifest['declared_native_management_request']['exit']['time_in_force'] = ''
    elif change == 'entry': manifest['declared_native_entry_request']['capital_request']['fraction'] = [1, 2]
    elif change == 'assignments': manifest['declared_native_assignment_source']['permissions']['add'] = False
    elif change == 'fees': envelope['payload']['private_free_fills'] = True
    elif change == 'cash': envelope['payload']['private_cash'] = 100000
    elif change == 'candidate_only': envelope = deepcopy(x.envelope)
    seal(envelope)
    if change == 'count_alias': envelope['node_count'] = float(envelope['node_count'])
    x.source.queries.clear()
    x.managed_resolver._envelope = json.dumps(envelope)
    with pytest.raises(ValueError): x.managed_resolver.reconstruct_entry_facts(x.proposal)
    assert x.source.queries == []


@pytest.mark.parametrize('field,value', [('configuration_hash', 'f'*64), ('market_plan_token', 'f'*64),
    ('strategy_revision', True), ('mode', 'live')])
def test_actual_run_fence_rechecked_for_every_managed_read(managed, field, value):
    x = managed
    x.context[field] = value
    x.source.queries.clear()
    with pytest.raises(ValueError, match='fenced own run'):
        x.managed_resolver.reconstruct_entry_facts(x.proposal)
    assert x.source.queries == []


def test_candidate_drift_cannot_select_producer_policy_outside_sealed_spec(managed):
    x = managed
    x.managed_resolver.candidate = replace(x.spec, labels=replace(x.spec.labels, name='foreign'))
    with pytest.raises(ValueError, match='complete declaration'):
        x.managed_resolver.reconstruct_entry_facts(x.proposal)


def test_partial_spec_is_not_accepted_by_managed_reader(managed):
    x = managed
    with pytest.raises(ValueError, match='exact complete'):
        PreparedDeclaredManagedSourceResolver(x.client, run_id=x.context['run_id'],
            spec=x.managed_spec.execution, envelope=x.managed_envelope, approval=APPROVAL, market=x.market)


def test_source_reconstruction_does_not_open_native_entry_or_recovery(managed):
    from src.backend.backtest_declared_native_fixed_management import DeclaredManagementState
    x = managed
    p = x.proposal
    with pytest.raises(ValueError, match='entry admission remains closed'):
        x.managed_resolver.reconstruct_entry(p)
    state = DeclaredManagementState(p.run_id, p.source_token, 41000,
        (((p.account_id, p.assignment_id, p.ticker), p),), (), (), (), (), ())
    assert x.managed_resolver.reconstruct_manager_sources(state)[0].configuration_hash == x.managed_envelope['payload_hash']
    with pytest.raises(ValueError, match='restore remains closed'):
        x.managed_resolver.verify_manager_state(state)
