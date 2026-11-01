import numpy as np
import pytest
from research.vectorized_backtest.v61.ticker_selection import specialist_pools


def oracle(ordered,valid,active,ids):
    rows=[{} for _ in ids]
    for session in ordered:
        for i,values in enumerate(session['ticker_score']):
            for k,v in values.items():rows[i][str(k)]=rows[i].get(str(k),0.)+v
    pools={}
    for i,row in enumerate(rows):
        if valid[i] and active[i]:
            for k,v in row.items():pools.setdefault(k,[]).append((i,v))
    return {k:[i for i,v in sorted(pools[k],key=lambda x:(-x[1],ids[x[0]]))] for k in sorted(pools)}


def test_exact_specialist_order_including_ties_and_aliases():
    rng=np.random.default_rng(721);n=129;ids=rng.permutation(n);valid=rng.random(n)>.2;active=rng.random(n)>.1
    sessions=[dict(ticker_score=[{str(k):float(v) for k,v in zip(rng.choice(20,8,replace=False),rng.normal(size=8)*1e8)} for _ in range(n)]) for _ in range(24)]
    sessions[0]['ticker_score'][1]={1:1e16,'1':-1e16,'2':0.}
    sessions[1]['ticker_score'][1]={'1':1.}
    assert specialist_pools(sessions,valid,active,ids)==oracle(sessions,valid,active,ids)
    assert specialist_pools([dict(ticker_score=[{}]*n)],valid,active,ids)=={}
    sessions[0]['ticker_score'][0]={'x':float('nan')}
    with pytest.raises(ValueError,match='Nonfinite'):specialist_pools(sessions,valid,active,ids)
