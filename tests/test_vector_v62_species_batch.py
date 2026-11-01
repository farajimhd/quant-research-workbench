import numpy as np
from research.vectorized_backtest.v62.species_distance import DistanceIndex


def test_population_distance_exact_with_incremental_representatives():
    rng=np.random.default_rng(212)
    rows=[dict(features=[str(j) for j in rng.choice(40,8,replace=False)],parameters={str(j):float(rng.normal()) for j in rng.choice(30,6,replace=False)},shape=rng.normal(size=65).tolist(),behavior=rng.normal(size=192).tolist(),active=bool(i%3)) for i in range(525)]
    old=DistanceIndex({1:rows[0],2:rows[7]});new=DistanceIndex({1:rows[0],2:rows[7]});new.prepare_population(rows)
    for i,a in enumerate(rows):
        if i==37:old.add(3,rows[37]);new.add(3,rows[37])
        assert old.distances(a)==new.distances(a)
