import numpy as np
from research.vectorized_backtest.v32.species import distance
from research.vectorized_backtest.v32.species_distance import DistanceIndex


def test_packed_distances_are_bit_exact_with_incremental_representatives():
    rng=np.random.default_rng(2020)
    rows=[]
    for i in range(80):
        rows.append(dict(features=[str(x) for x in rng.choice(200,70,replace=False)],
            shape=rng.normal(size=65).tolist(),behavior=rng.uniform(0,20,size=131).tolist(),
            parameters={str(k):float(v) for k,v in zip(rng.choice(100,30,replace=False),rng.uniform(-16,16,30))},active=i%3!=0))
    reps={i:r for i,r in enumerate(rows[:32])};packed=DistanceIndex(reps)
    assert DistanceIndex({}).distances(rows[0])==[]
    for a in rows:
        expected=[(distance(a,b).hex(),sid) for sid,b in reps.items()]
        assert [(v.hex(),sid) for v,sid in packed.distances(a)]==expected
    for i,r in enumerate(rows[32:64],32):reps[i]=r;packed.add(i,r)
    for a in rows:
        assert [(v.hex(),sid) for v,sid in packed.distances(a)]==[(distance(a,b).hex(),sid) for sid,b in reps.items()]
    inactive=dict(rows[0]);inactive.pop('behavior');inactive['active']=False
    reps={0:inactive,1:rows[1]};packed=DistanceIndex(reps)
    for a in (inactive,rows[1]):
        assert [(v.hex(),sid) for v,sid in packed.distances(a)]==[(distance(a,b).hex(),sid) for sid,b in reps.items()]
