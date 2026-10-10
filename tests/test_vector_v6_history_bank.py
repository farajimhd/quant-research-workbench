import numpy as np
from research.vectorized_backtest.v6.torch_backtest.history_bank import calculate,compress,SWING_WINDOWS


def test_causal_windows_gaps_and_swing_confirmation():
    n=160
    mark=np.arange(n,dtype=float)+10;low=mark-1;high=mark+1;observed=np.ones(n,dtype=bool)
    observed[20]=False;low[80]=1
    values=calculate(mark,high,low,observed,np.ones(n))
    for w in range(1,61):
        for t in (0,19,20,21,79,140):
            prior=np.where(observed,mark,np.nan)
            expected=prior[t-w] if t>=w else np.nan
            np.testing.assert_equal(values[t,60+w-1],expected)
            window=np.where(observed,high,np.nan)[max(0,t-w):t]
            np.testing.assert_equal(values[t,w-1],np.max(window) if t>=w else np.nan)
    col=211+SWING_WINDOWS.index(5)*len(SWING_WINDOWS)+SWING_WINDOWS.index(10)
    assert np.isnan(values[90,col])
    assert values[91,col]==1
    assert np.isnan(values[21,180])
    assert values[140,120+59]==1


def test_future_changes_cannot_change_prefix():
    rng=np.random.default_rng(23);n=200
    mark=rng.uniform(10,20,n);high=mark+1;low=mark-1;observed=np.ones(n,dtype=bool);notional=rng.uniform(0,100,n)
    before=calculate(mark,high,low,observed,notional)
    mark[100:]+=100;high[100:]+=100;low[100:]+=100;notional[100:]+=100
    after=calculate(mark,high,low,observed,notional)
    np.testing.assert_array_equal(before[:100],after[:100])


def test_compression_preserves_bits_and_clock_identity():
    a=np.array([[np.nan,0.],[np.nan,0.],[np.nan,-0.],[2.,3.]])
    rows,ids=compress(a)
    np.testing.assert_array_equal(rows[ids].view(np.uint64),a.view(np.uint64))
