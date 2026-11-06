"""Reproduction-local reuse of successful validation for immutable networks only."""
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar

_active = ContextVar('v42_network_validation', default=None)
CAPACITY = 4096
_capacity = ContextVar('v57_validation_capacity', default=CAPACITY)
_topology = ContextVar('v57_topology_cache',default=None)


def known_immutable(network):
    cache = _active.get()
    return cache is not None and cache.get(id(network)) is network


@contextmanager
def validation_scope(*, capacity=CAPACITY, certificate=()):
    if type(capacity) is not int or not 1<=capacity<=32768:
        raise ValueError('Invalid bounded validation cache capacity')
    token = _active.set(OrderedDict((id(n),n) for n in certificate))
    limit = _capacity.set(capacity)
    topology=_topology.set(OrderedDict())
    try:
        from .identity_cache import identity_scope
        with identity_scope(capacity=capacity):
            yield
    finally:
        _active.reset(token)
        _capacity.reset(limit)
        _topology.reset(topology)


def certificate(members):
    """Retain only successful, structurally immutable validations from this scope."""
    return tuple({id(n):n for m in members for n in m.networks.values() if known_immutable(n)}.values())


def topology(network,build):
    cache=_topology.get();key=id(network)
    if cache is not None and key in cache and cache[key][0] is network:return cache[key][1]
    value=build()
    if cache is not None and (known_immutable(network) or immutable(network)):
        cache[key]=(network,value)
        if len(cache)>_capacity.get():cache.popitem(last=False)
    return value


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
        if len(cache) > _capacity.get():
            cache.popitem(last=False)
    return result
