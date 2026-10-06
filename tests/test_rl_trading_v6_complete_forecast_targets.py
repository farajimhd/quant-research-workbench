import numpy as np
import polars as pl
import pytest
from research.rl_trading.v6.prepare_complete_forecast_targets import target_frame,align


def test_exact_forecasts_cover_held_and_preserve_original_flat_targets():
    frame=pl.DataFrame(dict(listing_id=['A']*3,time_us=[100,400,900],pair_id=[1,1,2],
        teacher_probabilities=[[.9,.1,0.,0.],[0.,0.,.2,.8],[0.,1.,0.,0.]],action=['ENTRY','EXIT','WAIT'],
        exit_gain=[.1,.2,.3],exit_quality=[.2,.95,.1],reference_action=['HOLD','EXIT','HOLD']))
    table=target_frame(frame,'day')
    data=dict(episode=['day:A:pair:1']*2,clock=np.array([100,400]),action=np.array([0,3]),future=np.array([[0,3,1,-1,-1],[-1]*5]))
    result=align(data,table)
    assert result['future'].tolist()==[[0,3,1,-1,-1],[3,-1,-1,-1,-1]]
    assert result['future_clock'][0].tolist()==[100,400,900,0,0]
    assert result['future_quality'][1,0]==pytest.approx(.95)
    wrong={**data,'action':np.array([0,2])}
    with pytest.raises(ValueError,match='parity'):align(wrong,table)
    with pytest.raises(ValueError,match='Duplicate'):align(data,pl.concat([table,table.head(1)]))
