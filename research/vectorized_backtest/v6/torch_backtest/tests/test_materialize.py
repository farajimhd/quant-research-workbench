import numpy as np
import polars as pl
from io import StringIO
from research.vectorized_backtest.v6.torch_backtest.materialize import rank_indices, rolling_volume
from research.vectorized_backtest.v6.torch_backtest.materialize import BAR_SCHEMA


def test_fractional_share_volume_uses_explicit_float_schema():
    # A long integer-valued prefix must not determine the later volume type.
    prefix='X,1,1,1,1,100,1,100,100,1,1\n'*150
    csv=','.join(BAR_SCHEMA)+'\n'+prefix+'X,2,1,1,1,0.477459,1,0.061984,0.061984,1,1\n'
    rows=pl.read_csv(StringIO(csv),schema_overrides=BAR_SCHEMA)
    assert rows['volume'][-1]==.477459
    assert rows['execution_volume'][-1]==.061984


def test_elapsed_window_and_prefix_invariance():
    values=np.array([[5.,0.],[0.,6.],[0.,0.],[0.,1.]])
    expected=np.array([[5.,0.],[5.,6.],[0.,6.],[0.,1.]])
    np.testing.assert_array_equal(rolling_volume(values,2),expected)
    np.testing.assert_array_equal(rolling_volume(values[:2],2),expected[:2])


def test_stable_ties_unavailable_and_zero_are_not_admitted():
    values=np.array([[5.,5.,100.],[0.,0.,0.]])
    indices,_=rank_indices(values,np.array([[True,True,False],[True,True,True]]),1,3)
    np.testing.assert_array_equal(indices,np.array([[0,1,-1],[-1,-1,-1]]))
