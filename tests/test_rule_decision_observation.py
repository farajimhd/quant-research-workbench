"""Shared scalar observation seals, exact float bits and causal relationships."""
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest

from src.trading_runtime.rule_decision_basis_schema import OBSERVATION, OBSERVED_ORDER, UNOBSERVED_ORDER
from src.trading_runtime.rule_decision_basis_projection import project_rule_decision_basis, _seal, rekey_rule_decision_basis
from src.trading_runtime.rule_decision_observation import (
    project_decision_observation, restore_decision_observation, verify_decision_observation_graph,
    observation_digest, qualified_observation_rows)
from src.trading_runtime.oms_assignment_observation import AssignmentOrderObservation, ObservedWorkingOrder
from tests.test_rule_decision_basis_projection import values
from types import SimpleNamespace


def inputs():
    basis, _ = project_rule_decision_basis(values('wait'), ())
    observation = AssignmentOrderObservation('account', 'assignment', 'ABC', values()['boundary_at'],
        False, True, ('unknown',), (ObservedWorkingOrder('stop', 'protective_stop', 1.0000000000000002,
            0., 10.000000000000002, 'Submitted'),), 10.000000000000002)
    return basis, observation


def test_shared_rows_restore_native_bits_and_empty_observation_is_distinct_from_missing():
    basis, observation = inputs()
    root, orders, unknown = project_decision_observation(basis, observation)
    recovered = restore_decision_observation(basis, root, orders, unknown)
    assert recovered == observation
    assert recovered.working_orders[0].stop_price.hex() == observation.working_orders[0].stop_price.hex()
    verify_decision_observation_graph({table.name: rows for table, rows in
        ((OBSERVATION, (root,)), (OBSERVED_ORDER, orders), (UNOBSERVED_ORDER, unknown))}, (basis,))
    empty = replace(observation, working_orders=(), unobserved_order_ids=())
    assert restore_decision_observation(basis, *project_decision_observation(basis, empty)) == empty
    assert all(all(kind not in {'JSON', 'Object', 'Map(String, String)'} for _, kind in table.columns)
        for table in (OBSERVATION, OBSERVED_ORDER, UNOBSERVED_ORDER))


@pytest.mark.parametrize('failure', ['hash', 'child_count', 'missing_child', 'extra_child', 'parent', 'future', 'duplicate_root'])
def test_observation_corruption_or_foreign_parent_never_passes(failure):
    basis, observation = inputs()
    root, orders, unknown = project_decision_observation(basis, observation)
    if failure == 'hash': root = {**root, 'pending_reduction': 0}
    elif failure == 'child_count': root = _seal(OBSERVATION, {**{key: value for key, value in root.items()
        if key != 'content_hash'}, 'working_order_count': 0})
    elif failure == 'missing_child': orders = ()
    elif failure == 'extra_child': orders += orders
    elif failure == 'parent': basis = {**basis, 'record_id': str(uuid4())}
    elif failure == 'future': basis = {**basis, 'boundary_at': observation.observed_at + timedelta(seconds=1)}
    if failure == 'duplicate_root':
        with pytest.raises(ValueError):
            verify_decision_observation_graph({OBSERVATION.name: (root, root)}, (basis,))
    else:
        with pytest.raises(ValueError): restore_decision_observation(basis, root, orders, unknown)


def test_native_proof_hash_is_rechecked_and_only_batch_rekeying_is_allowed():
    basis, observation = inputs()
    unit = SimpleNamespace(basis=basis, intents=())
    proof = SimpleNamespace(observation=observation, observation_hash=observation_digest(observation))
    qualified = SimpleNamespace(_proof=proof)
    rekeyed, _ = rekey_rule_decision_basis(basis, (), batch_id=str(uuid4()))
    rows = dict(qualified_observation_rows(unit, qualified, rekeyed_basis=rekeyed))
    assert rows[OBSERVATION.name][0]['batch_id'] == rekeyed['batch_id']
    with pytest.raises(ValueError, match='causal basis'):
        qualified_observation_rows(unit, qualified, rekeyed_basis={**rekeyed, 'assignment_id': 'foreign'})
    proof.observation = replace(observation, pending_reduction=False)
    with pytest.raises(ValueError, match='changed after'):
        qualified_observation_rows(unit, qualified)


@pytest.mark.parametrize('failure', [None, 'missing_root', 'missing_order', 'foreign_order', 'row_bound'])
def test_bounded_committed_observation_join_uses_native_child_and_parent_seals(monkeypatch, failure):
    from src.backend import rule_decision_recovery as reader
    from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
    basis, observation = inputs()
    root, orders, unknown = project_decision_observation(basis, observation)
    prefix = V4CommittedPrefix('run', 5, basis['batch_id'], 'cursor', 'completed', (basis['batch_id'],))
    families = {OBSERVATION.name: (root,), OBSERVED_ORDER.name: orders, UNOBSERVED_ORDER.name: unknown}
    if failure == 'missing_root': families[OBSERVATION.name] = ()
    elif failure == 'missing_order': families[OBSERVED_ORDER.name] = ()
    elif failure == 'foreign_order': families[OBSERVED_ORDER.name] = ({**orders[0], 'parent_record_id': str(uuid4())},)
    queries = []
    def rows(client, query):
        assert 'batch_id IN' in query and "run_id='run'" in query and 'LIMIT ' in query
        queries.append(query)
        return families[query.split('FROM arte.')[1].split()[0]]
    monkeypatch.setattr(reader, '_rows', rows)
    kwargs = dict(maximum_children=1 if failure == 'row_bound' else 2)
    if failure:
        with pytest.raises(ValueError): reader.load_decision_observation_page(object(), prefix, ((basis, ()),), **kwargs)
    else:
        result = reader.load_decision_observation_page(object(), prefix, ((basis, ()),), **kwargs)
        assert result == ((basis, (), observation),) and len(queries) == 3
