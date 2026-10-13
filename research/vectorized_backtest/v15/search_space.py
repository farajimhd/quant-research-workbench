"""Data-independent, typed sampling priors; no past results or validation reads."""
from collections import defaultdict
import numpy as np
from .features import CATALOG
from research.vectorized_backtest.v6.torch_backtest.program import Op, WINDOWS

VERSION='v15-random-typed-space-v1'
MAXIMUM_AGE=300  # Existing evaluator clock units (seconds), holding stages only.
FRACTIONS=('stop_fraction','target_fraction','reduce_fraction','trail_fraction',
           'add_minimum_profit','reduce_minimum_profit')

def family(feature):
    name=feature.name
    if name.startswith('history.'):return 'history',name.split('.')[1]
    if name.startswith('v7.'):return 'levels',name.split('.')[-1]
    if name.startswith('market.'):return 'market',name.split('.')[1]
    return 'base',name

_groups=defaultdict(lambda:defaultdict(list))
for _i,_f in enumerate(CATALOG):
    _domain,_family=family(_f);_groups[_domain][_family].append(_i)
GROUPS=tuple(tuple(tuple(indices) for indices in families.values()) for families in _groups.values())

def feature_index(rng):
    """Uniform domain, then family, then member; window count is not family weight."""
    domain=GROUPS[int(rng.integers(len(GROUPS)))]
    members=domain[int(rng.integers(len(domain)))]
    return members[int(rng.integers(len(members)))]

def semantic(feature):
    if feature.name.startswith('history.average_move.') or feature.name=='market.spread':
        return 'magnitude' if feature.unit=='price' else 'shifted_magnitude'
    return feature.unit

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
    # Full positive fraction envelope, with resolution around small values.
    if name in ('add_minimum_profit','reduce_minimum_profit') and rng.random()<.15:return 0.
    if rng.random()<.05:return 1.
    return float(np.exp(rng.uniform(np.log(1e-4),0.))) if rng.random()<.7 else float(rng.uniform(1e-4,1.))

def age(rng):
    return 0 if rng.random()<.5 else int(rng.integers(1,MAXIMUM_AGE+1))

COMPARISONS=(Op.GREATER,Op.LESS,Op.GREATER_EQUAL,Op.LESS_EQUAL,Op.CROSS_ABOVE,Op.CROSS_BELOW)
TEMPORAL=(Op.LAG,Op.MEAN,Op.MINIMUM,Op.MAXIMUM,Op.DIFFERENCE)
