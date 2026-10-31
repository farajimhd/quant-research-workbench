"""Immutable construction contract shared by all V60 candidate producers.

Bounds constrain construction, not observed market values. Availability remains
causal: a supported feature can still be unavailable at a particular candle.
"""
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
from types import MappingProxyType
import numpy as np

_SOURCE = Path(__file__).with_name('feature_registry.json').read_bytes()
_DOCUMENT = json.loads(_SOURCE)
VERSION = _DOCUMENT['version']+'-market-only-v4'
REGISTRY_SHA256 = hashlib.sha256(_SOURCE).hexdigest()
MAX_NODES = 1024
MAX_TOTAL_NODES = 1024
MAX_FAN_IN = 16
SWING_WINDOWS = (1, 2, 3, 4, 5, 10, 15, 20, 30, 45, 60)


@dataclass(frozen=True)
class FeatureHandle:
    id: int
    name: str
    unit: str
    allowed: bool
    lower: float | None
    upper: float | None
    basis: str
    history: str
    readiness: str

    def clamp(self, value):
        if not self.allowed:
            raise ValueError('Forbidden feature has no construction handle')
        if not math.isfinite(value):
            raise ValueError('Nonfinite construction parameter')
        return min(self.upper, max(self.lower, float(value)))


FEATURES = tuple(FeatureHandle(**{k: r[k] for k in FeatureHandle.__dataclass_fields__})
                 for r in _DOCUMENT['features'])
BY_NAME = MappingProxyType({f.name: f for f in FEATURES if f.allowed})
ALLOWED = tuple(f.id for f in FEATURES if f.allowed)
BLOCKED = frozenset(f.id for f in FEATURES if not f.allowed)
assert len(FEATURES) == 957 and len(ALLOWED) == 834
assert all(f.id == i for i, f in enumerate(FEATURES))


def feature(name):
    """Forbidden/unknown names cannot be supplied to a constructor."""
    try:
        return BY_NAME[name]
    except KeyError:
        raise ValueError(f'Unsupported V60 input: {name}') from None


def feature_index(rng):
    group = GROUPS[int(rng.integers(len(GROUPS)))]
    return group[int(rng.integers(len(group)))]


def _family(f):
    parts = f.name.split('.')
    if parts[0] == 'enriched':
        return ('enriched', parts[1], parts[-1] if parts[1] in ('candle', 'context') else parts[1])
    if parts[0] == 'indicator':
        return ('indicator', parts[1], f.unit)
    if parts[0] == 'history':
        return ('history', parts[1])
    return (parts[0], parts[1]) if parts[0] == 'market' else ('base', f.name)


_groups = {}
for _f in FEATURES:
    if _f.allowed:
        _groups.setdefault(_family(_f), []).append(_f.id)
GROUPS = tuple(tuple(g) for g in _groups.values())


def fraction(rng, name):
    if name in ('add_minimum_profit', 'reduce_minimum_profit') and rng.random() < .15:
        return 0.
    upper = .9999 if name == 'stop_fraction' else 1.
    if rng.random() < .5:
        return float(rng.uniform(1e-4, upper))
    return float(np.exp(rng.uniform(np.log(1e-4), np.log(upper))))


@lru_cache(maxsize=2048)
def legal_sources(node_index, *, entry=False):
    if not 0 <= node_index < MAX_NODES:
        raise ValueError('Node index outside construction budget')
    from .config import STATES
    count = len(FEATURES)
    # Slots 7..10 are reserved for layout compatibility, never model inputs.
    state = ()  # No position inputs in any action head
    sources = np.asarray(tuple(i + channel * count for channel in range(3)
                               for i in ALLOWED) + state +
                         tuple(range(3 * count + len(STATES),
                                     3 * count + len(STATES) + node_index)), dtype=np.int32)
    sources.flags.writeable = False
    return sources


def dependency_certificate(member):
    """One certificate for all routed leaves, with a fixed feature identity."""
    from .strategy_mix import leaves
    from .genome import MARKET_INPUTS, INPUTS
    used = set()
    total = 0
    for leaf in leaves(member):
        for stage, net in leaf.networks.items():
            total += len(net.nodes)
            net.validate()
            sources=np.fromiter((s for n in net.nodes for s in n.sources), dtype=np.int32)
            market=sources[sources<MARKET_INPUTS] % len(FEATURES)
            used.update(np.unique(market).tolist())
            if any(i in BLOCKED for i in used):
                raise ValueError('Source outside V60 construction authority')
            states=sources[(sources>=MARKET_INPUTS)&(sources<INPUTS)]-MARKET_INPUTS
            if len(states):
                raise ValueError('Order feedback/time or flat-entry state input is forbidden')
        if (leaf.policy.swing_left, leaf.policy.swing_right) != (0, 0):
            if any(x not in SWING_WINDOWS for x in (leaf.policy.swing_left, leaf.policy.swing_right)):
                raise ValueError('Unsupported swing history')
    if total > MAX_TOTAL_NODES:
        raise ValueError('Total network budget exceeded')
    return dict(version=VERSION, registry_sha256=REGISTRY_SHA256,
                features=sorted(used), total_nodes=total,
                dependencies=[dict(id=i, history=FEATURES[i].history,
                                   readiness=FEATURES[i].readiness) for i in sorted(used)])


def coverage(values, valid, columns=None):
    """Vectorized training-only cap diagnostic; never clips input observations."""
    values = np.asarray(values)
    valid = np.asarray(valid, dtype=bool)
    columns = tuple(range(len(FEATURES))) if columns is None else tuple(columns)
    if values.shape != valid.shape or values.shape[-1] != len(columns):
        raise ValueError('Catalog-aligned values and masks required')
    lo = np.array([f.lower if f.allowed else -np.inf for f in (FEATURES[i] for i in columns)])
    hi = np.array([f.upper if f.allowed else np.inf for f in (FEATURES[i] for i in columns)])
    known = valid & np.isfinite(values)
    axes = tuple(range(values.ndim - 1))
    return dict(observed=known.sum(axis=axes), below=(known & (values < lo)).sum(axis=axes),
                above=(known & (values > hi)).sum(axis=axes))
