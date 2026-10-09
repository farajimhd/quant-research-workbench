import numpy as np
from research.vectorized_backtest.v6.torch_backtest.materialize import rank_indices, rolling_volume


def test_elapsed_window_and_prefix_invariance():
    values=np.array([[5.,0.],[0.,6.],[0.,0.],[0.,1.]])
    expected=np.array([[5.,0.],[5.,6.],[0.,6.],[0.,1.]])
    np.testing.assert_array_equal(rolling_volume(values,2),expected)
    np.testing.assert_array_equal(rolling_volume(values[:2],2),expected[:2])


def test_stable_ties_unavailable_and_zero_are_not_admitted():
    values=np.array([[5.,5.,100.],[0.,0.,0.]])
    indices,_=rank_indices(values,np.array([[True,True,False],[True,True,True]]),1,3)
    np.testing.assert_array_equal(indices,np.array([[0,1,-1],[-1,-1,-1]]))
