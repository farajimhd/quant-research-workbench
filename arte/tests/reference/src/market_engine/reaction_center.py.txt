"""Retrospective, role-specific Student-t reaction-price estimates; no band edits."""
from copy import deepcopy
import math

import numpy as np
from scipy.optimize import minimize

VERSION = 'reaction-center-student-t-1'
CONFIG = dict(version=VERSION, degrees_of_freedom=4, minimum_observations=3,
              scale_floor_ticks=.5, observation='1s extreme from contact through resolution',
              overlap_policy='first nonoverlapping resolved rejection per role')

SOLVER_VERSION = 'student-t-analytic-gradient-1'


def objective_gradient(parameters, prices, degrees_of_freedom=4):
    """Mean negative log likelihood and exact derivatives in tick coordinates.

    Omits constants independent of the parameters. Averaging leaves the MLE
    unchanged and makes convergence checks independent of observation count.
    """
    location, log_scale = parameters
    scale = np.exp(log_scale)
    z = (prices-location)/scale
    denominator = degrees_of_freedom+z*z
    value = log_scale+(degrees_of_freedom+1)/2*np.log1p(z*z/degrees_of_freedom).mean()
    gradient = np.array([-(degrees_of_freedom+1)*(z/denominator).mean()/scale,
                         1-(degrees_of_freedom+1)*(z*z/denominator).mean()])
    return float(value), gradient


def converged(result, objective, bounds):
    """Require successful termination and independently checked box stationarity."""
    if not result.success or not np.isfinite(result.fun) or not np.isfinite(result.x).all():
        return False
    lower, upper = np.asarray(bounds).T
    if np.any(result.x < lower) or np.any(result.x > upper):
        return False
    value, gradient = objective(result.x)
    projected = result.x-np.clip(result.x-gradient, lower, upper)
    return bool(np.isfinite(value) and np.isfinite(gradient).all() and np.max(np.abs(projected)) <= 1e-6)


def annotate(events, bars):
    """Retain source observations, including rejected/overlapping ones for audit."""
    times=np.asarray([b['t'] for b in bars])
    result=deepcopy(events)
    for event in result:
        event['reaction_price']=None
        event['reaction_at']=None
        if event['outcome']!='rejection':
            continue
        lo=int(np.searchsorted(times,event['at']))
        hi=int(np.searchsorted(times,event['resolved_at'],side='right'))
        if lo>=hi or times[lo]!=event['at'] or times[hi-1]!=event['resolved_at']:
            raise ValueError('Reaction evidence timestamps missing from source bars')
        key='high' if event['role']=='resistance' else 'low'
        window=bars[lo:hi]
        turning=(max if key=='high' else min)(window,key=lambda b:b[key])
        event['reaction_price']=float(turning[key])
        event['reaction_at']=turning['t']
    return result


def fit(prices, tick):
    """Fixed df=4 MLE with a tick-based scale floor and deterministic multistart."""
    x=np.asarray(prices,dtype=float)
    if not math.isfinite(tick) or tick<=0 or not np.isfinite(x).all() or np.any(x<=0):
        raise ValueError('Invalid reaction prices or tick')
    result=dict(count=len(x),center=None,scale=None,status='insufficient_evidence')
    if len(x)<CONFIG['minimum_observations']:
        return result
    origin=float(np.median(x));y=(x-origin)/tick
    floor=CONFIG['scale_floor_ticks'];nu=CONFIG['degrees_of_freedom']
    def objective(p):
        return objective_gradient(p,y,nu)
    spread=max(floor,float(np.median(np.abs(y)))*1.4826)
    bounds=[(float(y.min()),float(y.max())),(math.log(floor),math.log(max(1.,float(np.ptp(y))*2)))]
    trials=[minimize(objective,[float(loc),math.log(spread)],jac=True,method='L-BFGS-B',bounds=bounds,
                     options=dict(gtol=1e-8,ftol=0.,maxls=50,maxiter=1000))
            for loc in np.unique(np.quantile(y,[.25,.5,.75]))]
    valid=[r for r in trials if converged(r,objective,bounds)]
    if not valid:
        return dict(result,status='fit_failed')
    best=min(valid,key=lambda r:(r.fun,float(r.x[0])))
    return dict(result,center=float(origin+tick*best.x[0]),scale=float(tick*np.exp(best.x[1])),
                status='estimated',scale_at_floor=bool(np.exp(best.x[1])<=floor*1.00001))


def estimate(contributions,tick):
    roles={}
    for role in ('support','resistance'):
        prices=[];overlap=0;missing=0;unresolved=0;crossings=0
        for day in contributions:
            end=-float('inf');factor=day.get('reaction_price_factor',1.)
            if not math.isfinite(factor) or factor<=0:
                raise ValueError('Invalid reaction price adjustment')
            for e in sorted(day['encounters'],key=lambda e:(e['at'],e['resolved_at'])):
                if e['role']!=role:continue
                if e['outcome']!='rejection':
                    crossings+=e['outcome']=='acceptance';unresolved+=e['outcome']=='unresolved';continue
                if e.get('reaction_price') is None:missing+=1;continue
                if e['at']<=end:overlap+=1;continue
                prices.append(e['reaction_price']*factor);end=e['resolved_at']
        roles[role]=dict(fit(prices,tick),overlapping_excluded=overlap,missing_prices=missing,
                         accepted_crossings_excluded=crossings,unresolved_excluded=unresolved)
    # Do not force distinct support and resistance turning prices into one center.
    return dict(config=CONFIG.copy(),roles=roles)


def update(row,session,available_at,tick):
    value=estimate(row['contributions'],tick)
    value.update(session=session,available_at=available_at)
    row['reaction_center']=value
    row.setdefault('reaction_center_history',[]).append(deepcopy(value))
