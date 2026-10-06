import numpy as np
import polars as pl
import pytest
from research.rl_trading.v6.prepare_hierarchical_bias_targets import align_targets


def test_exact_original_and_suppressed_actions_align_without_target_inputs():
    data=dict(episode=['a','b','c','a'],clock=np.array([1,2,3,1]),action=np.array([0,1,1,3]))
    frame=pl.DataFrame(dict(episode=['c','a','b'],clock=[3,1,2],entry_1a=[False,True,True],exit_1a=[False,True,False],episode_selected=[None,True,False]))
    result=align_targets(data,frame)
    np.testing.assert_array_equal(result['entry_1a'],[True,True,False,True])
    np.testing.assert_array_equal(result['episode_selected'],[True,False,False,True])
    with pytest.raises(ValueError,match='Missing'):align_targets(data,frame.head(2))
    with pytest.raises(ValueError,match='Duplicate'):align_targets(data,pl.concat([frame,frame]))
    with pytest.raises(ValueError,match='suppressed'):align_targets({**data,'action':np.array([1,1,1,3])},frame)
    with pytest.raises(ValueError,match='EXIT'):align_targets({**data,'action':np.array([0,1,1,2])},frame)
