import numpy as np
from research.vectorized_backtest.v62.behavior_sketch import fill


def test_interval_sketch_matches_every_bin_including_tail_and_zero_duration():
    rng=np.random.default_rng(173)
    entries=np.r_[0,1799,1800,86399,86400,rng.integers(0,100000,5000)].astype(np.int64)
    exits=entries+rng.integers(0,100000,len(entries));exits[:5]=entries[:5]
    listings=rng.integers(0,300,len(entries));offsets=np.array([0,5,1000,len(entries)])
    actual=fill(offsets,entries,exits,listings);expected=np.zeros_like(actual)
    for i in range(len(offsets)-1):
        for k in range(offsets[i],offsets[i+1]):
            entry=entries[k];exit=exits[k]
            expected[i,min(entry//1800,47)]+=1;expected[i,96+listings[k]%32]+=1
            for b in range(48):expected[i,48+b]+=max(0,min(exit,(b+1)*1800)-max(entry,b*1800))
        expected[i,48:96]/=1800
    np.testing.assert_array_equal(actual,expected)
