from dataclasses import replace

import pytest

from research.vectorized_backtest.v32 import validation_cache as cache
from research.vectorized_backtest.v32.genome import Network, Neuron


def network():
    return Network((Neuron((0,), (1.,), 0.),))


def test_scope_reuses_only_successful_immutable_validation(monkeypatch):
    original = Network._validate_uncached
    calls = []
    def counted(self):
        calls.append(self)
        return original(self)
    monkeypatch.setattr(Network, '_validate_uncached', counted)
    n = network()
    with cache.validation_scope():
        assert n.validate() is n
        n.validate()
        assert len(calls) == 1
        for _ in range(2):
            with pytest.raises(ValueError):
                replace(n, threshold=1.).validate()
        with pytest.raises(ValueError):
            replace(n, nodes=(replace(n.nodes[0], uid=-1),)).validate()
    n.validate()
    assert len(calls) == 5
    with cache.validation_scope():
        n.validate()
    assert len(calls) == 6


def test_mutable_containers_are_always_revalidated():
    weights = [1.]
    n = Network([Neuron((0,), weights, 0.)])
    with cache.validation_scope():
        n.validate()
        assert not cache._active.get()
        weights[0] = float('nan')
        with pytest.raises(ValueError):
            n.validate()


def test_bounded_cache_and_exception_cleanup(monkeypatch):
    monkeypatch.setattr(cache, 'CAPACITY', 2)
    a, b, c = network(), network(), network()
    with pytest.raises(RuntimeError):
        with cache.validation_scope():
            for n in (a, b, c):
                n.validate()
            assert len(cache._active.get()) == 2
            assert id(a) not in cache._active.get()
            assert cache._active.get()[id(c)] is c
            raise RuntimeError('test cleanup')
    assert cache._active.get() is None
