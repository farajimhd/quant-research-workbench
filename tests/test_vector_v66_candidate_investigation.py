import numpy as np
from research.vectorized_backtest.v66.candidate_investigation import price_causes


def test_missing_swing_is_separate_from_spread_and_stop_relation():
    prices=np.array([[10.,10.,np.nan,11.,9.],[10.,10.,9.99,11.,9.],[10.,10.,9.,np.nan,9.]])
    result=price_causes(prices,np.array([9.99,9.99,9.]),np.array([10.,10.,10.]))
    assert result['missing_stop'].tolist()==[True,False,False]
    assert result['invalid_stop'].tolist()==[False,True,True]
    assert result['missing_target'].tolist()==[False,False,True]
    assert result['excessive_spread'].tolist()==[False,False,True]


def test_exact500bps_is_allowed():
    result=price_causes(np.array([[10.5,10.,9.,12.,9.]]),np.array([10.]),np.array([10.5]))
    assert not any(v.any() for v in result.values())
