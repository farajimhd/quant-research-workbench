"""Reproduction-local reuse of successful validation for immutable networks only."""
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar

_active = ContextVar('v34_network_validation', default=None)
CAPACITY = 4096


def known_immutable(network):
    cache = _active.get()
    return cache is not None and cache.get(id(network)) is network


@contextmanager
def validation_scope():
    token = _active.set(OrderedDict())
    try:
        from .identity_cache import identity_scope
        with identity_scope():
            yield
    finally:
        _active.reset(token)


def immutable(network):
    from .genome import Network, Neuron
    scalar = (int, float)
    return (type(network) is Network and type(network.nodes) is tuple
        and all(type(v) in scalar for v in (network.threshold, network.size_source,
                                           network.size_weight, network.size_bias))
        and all(type(n) is Neuron and type(n.sources) is tuple and type(n.weights) is tuple
                and type(n.op) is str and type(n.uid) is int
                and all(type(s) is int for s in n.sources)
                and all(type(w) in scalar for w in (*n.weights, n.bias)) for n in network.nodes))


def validate(network, uncached):
    cache = _active.get()
    if cache is not None and cache.get(id(network)) is network:
        return network
    result = uncached()
    if cache is not None and immutable(network):
        # Strong references prevent identity reuse. FIFO eviction changes only
        # recomputation; invalid and externally mutable objects are never cached.
        cache[id(network)] = network
        if len(cache) > CAPACITY:
            cache.popitem(last=False)
    return result
