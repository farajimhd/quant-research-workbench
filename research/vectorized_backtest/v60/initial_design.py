"""Randomized stratified policy design in a jointly feasible domain.

Each marginal visits every stratum once; independent permutations avoid the
exponential Cartesian product. Network sizes retain their existing allocation.
"""
from dataclasses import replace
import numpy as np
from .authority import feasible_policy


def policies(rng,members):
    n=len(members)
    def axis():return (rng.permutation(n)+rng.random(n))/n
    stop=np.exp(np.log(1e-4)+axis()*np.log(.9999/1e-4))
    target=np.exp(np.log(1e-4)+axis()*np.log(1/1e-4))
    buy=axis();sell=-.01+.02*axis();cooldown=1+np.minimum(299,(axis()*300).astype(int))
    out=[]
    for i,m in enumerate(members):
        lower=max(-.01,-stop[i]+1e-6) if not m.policy.swing_left else -.01
        p=replace(m.policy,stop_fraction=float(stop[i]),target_fraction=float(target[i]),
            buy_limit_offset=float(lower+buy[i]*(.01-lower)),sell_limit_offset=float(sell[i]),cooldown=int(cooldown[i]))
        out.append(replace(m,policy=feasible_policy(p)).validate())
    return out
