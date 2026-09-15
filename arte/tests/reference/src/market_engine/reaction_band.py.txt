"""Per-level Student-t maximum likelihood geometry; no noise-width fallback."""
import numpy as np
from functools import lru_cache
from scipy.special import logsumexp
from scipy.stats import t
from .reaction_center import fit

CONFIG=dict(version='reaction-band-student-t-mle-1',degrees_of_freedom=4,coverage=.8,minimum_observations=3,
            scale_floor_resolution_multiple=.5,split_bic_improvement=10.)

@lru_cache(maxsize=4096)
def cached_fit(prices,resolution):
    return fit(prices,resolution)


def estimate(observations,coverage=.8):
    if not 0<coverage<1:raise ValueError('Band coverage must lie between zero and one')
    if not observations:return dict(status='insufficient_evidence',count=0)
    resolution=min(o['resolution'] for o in observations)
    result=dict(cached_fit(tuple(o['price'] for o in observations),resolution))
    result.update(coverage=coverage,resolution=resolution,distribution='student_t',degrees_of_freedom=4)
    if result['status']=='estimated':
        half=float(t.ppf((1+coverage)/2,4))*result['scale']
        result.update(lower=result['center']-half,upper=result['center']+half)
        if result['lower']<=0:result['status']='invalid_positive_price_interval'
    return result


def partition(observations,coverage=.8):
    """Test separated reaction clusters; each returned band is its own MLE.

    Candidate partitions are the three largest price gaps. BIC uses the actual
    mixture density, including mixing weights, not a sum of assigned likelihoods.
    This conservative split test is not a claim of a global mixture optimum.
    """
    ordered=sorted(observations,key=lambda o:(o['price'],o['resolved_at'],o['at']))
    one=estimate(ordered,coverage)
    if len(ordered)<6 or one['status']!='estimated':return [(ordered,one)]
    x=np.array([o['price'] for o in ordered]);n=len(x)
    baseline=-2*float(t.logpdf(x,4,loc=one['center'],scale=one['scale']).sum())+2*np.log(n)
    choices=sorted(range(3,n-2),key=lambda i:(-(x[i]-x[i-1]),i))[:3]
    best=None
    for i in choices:
        if x[i]-x[i-1]<=2*one['resolution']:continue
        left=estimate(ordered[:i],coverage);right=estimate(ordered[i:],coverage)
        if left['status']!='estimated' or right['status']!='estimated' or left['upper']>=right['lower']:continue
        terms=np.vstack([np.log(i/n)+t.logpdf(x,4,loc=left['center'],scale=left['scale']),np.log(1-i/n)+t.logpdf(x,4,loc=right['center'],scale=right['scale'])])
        bic=-2*float(logsumexp(terms,axis=0).sum())+5*np.log(n)
        if baseline-bic>CONFIG['split_bic_improvement'] and (best is None or bic<best[0]):best=(bic,i,left,right)
    if best is None:return [(ordered,one)]
    _,i,left,right=best
    return [(ordered[:i],left),(ordered[i:],right)]
