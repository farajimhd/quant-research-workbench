"""Engine controls only; these do not qualify native authority integration."""
from concurrent.futures import ThreadPoolExecutor

import pytest

from src.trading_runtime.bounded_verification_operation import BoundedVerificationOperation


def operation(fence=lambda: {'epoch': 1}, **bounds):
    return BoundedVerificationOperation(fence=fence, max_requests=bounds.get('requests', 2),
                                        max_bytes=bounds.get('bytes', 4096))


def test_same_request_reuses_and_returns_independent_values():
    calls = []
    with operation() as scope:
        def load():
            calls.append(1)
            return {'families': [{'hash': 'original'}]}
        first = scope.verify({'run': 'a', 'batch': 'b'}, load)
        first['families'][0]['hash'] = 'caller mutation'
        second = scope.verify({'batch': 'b', 'run': 'a'}, load)
        second['families'][0]['hash'] = 'another mutation'
        assert scope.verify({'run': 'a', 'batch': 'b'}, load)['families'][0]['hash'] == 'original'
        assert len(calls) == 1 and scope.hits == 2
    assert scope._retained == {} and scope._bytes == 0
    with pytest.raises(RuntimeError, match='closed'):
        scope.verify({}, load)


@pytest.mark.parametrize('field', ['client', 'run', 'source', 'predecessor', 'bounds', 'context'])
def test_distinct_request_fields_do_not_hit(field):
    with operation() as scope:
        original = {key: 'a' for key in ('client', 'run', 'source', 'predecessor', 'bounds', 'context')}
        assert scope.verify(original, lambda: {'value': 1}) == {'value': 1}
        changed = dict(original, **{field: 'b'})
        assert scope.verify(changed, lambda: {'value': 2}) == {'value': 2}
        assert scope.hits == 0


def test_failed_loader_is_not_retained():
    with operation() as scope:
        with pytest.raises(ValueError, match='failed'):
            scope.verify({}, lambda: (_ for _ in ()).throw(ValueError('failed')))
        assert scope._retained == {}
        assert scope.verify({}, lambda: {'success': True}) == {'success': True}


def test_owner_change_during_miss_discards_result():
    fence = {'epoch': 1}
    with operation(lambda: fence) as scope:
        def load():
            fence['epoch'] = 2
            return {'value': 1}
        with pytest.raises(RuntimeError, match='fence changed'):
            scope.verify({}, load)
        assert scope._retained == {} and scope._closed


def test_fence_failure_closes_operation_and_discards_proofs():
    state = {'failed': False}
    def fence():
        if state['failed']:
            raise RuntimeError('owner check failed')
        return {'epoch': 1}
    with operation(fence) as scope:
        scope.verify({}, lambda: {'value': 1})
        state['failed'] = True
        with pytest.raises(RuntimeError, match='owner check failed'):
            scope.verify({}, lambda: {'value': 2})
        assert scope._closed and scope._retained == {}


def test_retained_content_mutation_fails_closed():
    with operation() as scope:
        scope.verify({}, lambda: {'value': 1})
        next(iter(scope._retained.values()))[0]['value'] = 2
        with pytest.raises(RuntimeError, match='content changed'):
            scope.verify({}, lambda: {'value': 3})
        assert scope._closed


@pytest.mark.parametrize('bounds', [{'requests': 1}, {'bytes': 1}])
def test_budget_overflow_runs_complete_loader_without_truncation(bounds):
    calls = []
    with operation(**bounds) as scope:
        scope.verify({'batch': 'a'}, lambda: {'value': 1})
        def load():
            calls.append(1)
            return {'all_rows': list(range(20))}
        for _ in range(2):
            assert scope.verify({'batch': 'b'}, load) == {'all_rows': list(range(20))}
        assert len(calls) == 2 and scope.declined >= 2


def test_recursive_same_request_cannot_issue_partial_proof():
    with operation() as scope:
        with pytest.raises(RuntimeError, match='depends on itself'):
            scope.verify({}, lambda: scope.verify({}, lambda: {'value': 1}))
        assert scope._retained == {}


def test_cross_thread_rejected():
    with operation() as scope, ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(scope.verify, {}, lambda: {'value': 1})
        with pytest.raises(RuntimeError, match='another thread'):
            future.result()


@pytest.mark.parametrize('value', [object(), {1: 'wrong key'}, float('nan')])
def test_non_scalar_content_cannot_become_a_proof(value):
    with operation() as scope:
        with pytest.raises(ValueError):
            scope.verify({}, lambda: value)
        assert scope._retained == {}


def test_tuple_and_list_identity_are_distinct():
    with operation() as scope:
        assert scope.verify(('batch',), lambda: {'value': 1}) == {'value': 1}
        assert scope.verify(['batch'], lambda: {'value': 2}) == {'value': 2}
        assert scope.hits == 0


@pytest.mark.parametrize('requests,bytes_', [(True, 1), (1, False), (0, 1), (1, 0)])
def test_invalid_bounds_rejected(requests, bytes_):
    with pytest.raises(ValueError, match='bounds'):
        BoundedVerificationOperation(fence=lambda: {}, max_requests=requests, max_bytes=bytes_)
