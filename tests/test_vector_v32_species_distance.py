import numpy as np
from research.vectorized_backtest.v32.species import distance
from research.vectorized_backtest.v32.species_distance import DistanceIndex


def test_population_behavior_sketch_matches_original_integer_ledger():
    from research.vectorized_backtest.v32.species import training_behavior
    rng=np.random.default_rng(2020);n=37;ordered=[]
    for _ in range(3):
        entries=[np.sort(rng.integers(0,100000,size=int(rng.integers(0,60)))).tolist() for i in range(n)]
        entries[0]=[]
        ordered.append(dict(episode_entry_clocks=entries,
            episode_exit_clocks=[(np.asarray(x,dtype=np.int64)+rng.integers(0,5000,len(x))).tolist() for x in entries],
            episode_listings=[rng.integers(0,1000,len(x)).tolist() for x in entries],
            closed_positions=rng.integers(0,60,n).tolist(),
            capital_dollar_seconds=rng.uniform(0,1e12,n).tolist(),turnover=rng.uniform(0,1e8,n).tolist()))
    expected=[[] for _ in range(n)];active=np.zeros(n,dtype=bool)
    for m in ordered:
        for i in range(n):
            entries=np.asarray(m['episode_entry_clocks'][i],dtype=np.int64)
            exits=np.asarray(m['episode_exit_clocks'][i],dtype=np.int64)
            listings=np.asarray(m['episode_listings'][i],dtype=np.int64);active[i]|=len(entries)>0
            lower=np.arange(48)*1800
            holding=np.maximum(0,np.minimum(exits[:,None],lower+1800)-np.maximum(entries[:,None],lower)).sum(0)/1800
            expected[i].extend(np.log1p(np.r_[np.bincount(np.minimum(entries//1800,47),minlength=48),holding,
                np.bincount(listings%32,minlength=32),m['closed_positions'][i],m['capital_dollar_seconds'][i]/3600000,m['turnover'][i]/1000]).tolist())
    actual,flags=training_behavior(ordered,n)
    assert actual==expected
    np.testing.assert_array_equal(flags,active)


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
