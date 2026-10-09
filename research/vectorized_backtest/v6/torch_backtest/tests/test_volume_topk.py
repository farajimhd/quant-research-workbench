import numpy as np
import pytest
from research.vectorized_backtest.v6.torch_backtest.materialize import rank_indices,rolling_volume,stable_top_indices


@pytest.mark.parametrize('listings,k',[(1,10),(7,3),(31,10),(300,10)])
def test_partition_ties_and_missing_slots_match_full_stable_sort(listings,k):
    rng=np.random.default_rng(2236);volume=rng.integers(0,4,(137,listings)).astype(float)/8
    volume[:5]=0;volume[10:40]=0
    available=rng.random(volume.shape)>.1
    expected=rolling_volume(volume,30);expected[~available|(expected<=0)]=-np.inf
    ids=np.argsort(-expected,axis=1,kind='stable')[:,:min(k,listings)]
    values=np.take_along_axis(expected,ids,axis=1)
    ids=np.where(np.isfinite(values),ids,-1)
    actual,scores=rank_indices(volume,available,30,k)
    np.testing.assert_array_equal(actual,ids);np.testing.assert_array_equal(scores,values)
    for batch in (1,17,1024):
        a,b=stable_top_indices(expected,k,clock_batch=batch)
        np.testing.assert_array_equal(a,ids);np.testing.assert_array_equal(b,values)


def test_elapsed_volume_survives_missing_current_bar_and_never_uses_future():
    volume=np.zeros((35,2));volume[0,0]=100;volume[10,1]=1
    ids,_=rank_indices(volume,None,30,1)
    assert ids[29,0]==0 and ids[30,0]==1
    changed=volume.copy();changed[20:]=1e12
    np.testing.assert_array_equal(rank_indices(changed,None,30,1)[0][:20],ids[:20])


def test_nonfinite_cutoff_fails_instead_of_dropping_ranked_identity():
    with pytest.raises(ValueError,match='Non-finite ranked volume'):
        stable_top_indices(np.array([[np.inf,1.]]),1)
