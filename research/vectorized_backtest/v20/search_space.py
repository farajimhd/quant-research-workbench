"""Data-independent, typed sampling priors; no past results or validation reads."""
from collections import defaultdict
import numpy as np
from .features import CATALOG
from .program import Op, WINDOWS

VERSION='v18-independent-paths-full-expression-space-v1'
MAXIMUM_AGE=300  # Existing evaluator clock units (seconds), holding stages only.
FRACTIONS=('stop_fraction','target_fraction','reduce_fraction','trail_fraction',
           'add_minimum_profit','reduce_minimum_profit')

def family(feature):
    name=feature.name
    if name.startswith('enriched.'):
        parts=name.split('.')
        return 'enriched',parts[1],parts[-1] if parts[1] in ('candle','context') else parts[1]
    if name.startswith('indicator.'):return 'indicator',name.split('.')[1],feature.unit
    if name.startswith('history.'):return 'history',name.split('.')[1]
    if name.startswith('v7.'):return 'levels',name.split('.')[-1]
    if name.startswith('market.'):return 'market',name.split('.')[1]
    return 'base',name

_groups=defaultdict(list)
for _i,_f in enumerate(CATALOG):
    _groups[family(_f)].append(_i)
GROUPS=tuple(tuple(indices) for indices in _groups.values())

def feature_index(rng,groups=GROUPS):
    """Uniform eligible family globally, then channel; two O(1) draws."""
    if not groups:raise ValueError('No compatible feature families')
    members=groups[int(rng.integers(len(groups)))]
    return members[int(rng.integers(len(members)))]

def semantic(feature):
    if feature.name.startswith('history.average_move.') or feature.name=='market.spread':
        return 'magnitude' if feature.unit=='price' else 'shifted_magnitude'
    return feature.unit


def compatible_groups(indices,exclude):
    """Build once at import; never scan the catalog while sampling/mutating."""
    eligible=set(indices)-{exclude}
    return tuple(members for group in GROUPS if (members:=tuple(i for i in group if i in eligible)))


_prices=defaultdict(list);_substitutions=defaultdict(list)
for _i,_f in enumerate(CATALOG):
    _kind=semantic(_f)
    if _f.unit=='price':_prices[_kind].append(_i)
    _substitutions[(_f.unit,_kind,_f.lower,_f.upper)].append(_i)
PRICE_GROUPS=tuple(compatible_groups(_prices[semantic(f)],i) if f.unit=='price' else ()
                   for i,f in enumerate(CATALOG))
MUTATION_GROUPS=tuple(compatible_groups(_substitutions[(f.unit,semantic(f),f.lower,f.upper)],i)
                      for i,f in enumerate(CATALOG))


def comparison_feature(rng,index):
    return feature_index(rng,PRICE_GROUPS[index])


def mutation_feature(rng,index):
    groups=MUTATION_GROUPS[index]
    # A singleton compatibility class has no alternative, not an unsafe fallback.
    return feature_index(rng,groups) if groups else index

def threshold(rng,index):
    f=CATALOG[index];kind=semantic(f)
    if kind=='shifted_magnitude':
        # Persisted authority is average_move / close - 1. Do not reinterpret
        # cached values. Include the full declared upper range as a minority prior.
        return -1.+float(np.exp(rng.uniform(np.log(1e-6),np.log(1.5))))
    lo,hi=f.lower,f.upper
    if lo<0<hi and rng.random()<.7:
        bound=hi if rng.integers(2) else -lo
        return float(rng.choice((-1,1))*np.exp(rng.uniform(np.log(1e-6),np.log(bound))))
    if lo==0 and hi>1 and rng.random()<.7:
        return float(np.exp(rng.uniform(np.log(1e-6),np.log(hi))))
    return float(rng.uniform(lo,hi))

def fraction(rng,name):
    if name=='stop_fraction':
        return float(np.exp(rng.uniform(np.log(1e-4),np.log(.20)))) if rng.random()<.7 else float(rng.uniform(1e-4,.20))
    # Full positive fraction envelope, with resolution around small values.
    if name in ('add_minimum_profit','reduce_minimum_profit') and rng.random()<.15:return 0.
    if rng.random()<.05:return 1.
    return float(np.exp(rng.uniform(np.log(1e-4),0.))) if rng.random()<.7 else float(rng.uniform(1e-4,1.))

def age(rng):
    return 0 if rng.random()<.5 else int(rng.integers(1,MAXIMUM_AGE+1))

COMPARISONS=(Op.GREATER,Op.LESS,Op.GREATER_EQUAL,Op.LESS_EQUAL,Op.CROSS_ABOVE,Op.CROSS_BELOW)
TEMPORAL=(Op.LAG,Op.MEAN,Op.MINIMUM,Op.MAXIMUM,Op.DIFFERENCE,Op.STD)
