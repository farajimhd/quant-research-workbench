import polars as pl
from research.rl_trading.v6.market_teacher_dataset import copy_labels

def test_copy_suppresses_all_rejected_actions_preserving_originals_and_raw_values():
    original=pl.DataFrame(dict(listing_id=['a']*6,pair_id=[1,1,1,2,2,0],
        action=['ENTRY','HOLD','EXIT','ENTRY','EXIT','WAIT'],reference_action=['ENTRY','HOLD','EXIT','ENTRY','EXIT','WAIT'],
        label_value=[.8,.7,.9,.6,.5,1.],entry_gain=[1.]*6))
    decisions=pl.DataFrame(dict(listing_id=['a','a'],pair_id=[1,2],selected=[False,True],group_id=[None,2],
        allocation_ratio=[None,.4],active_score_sum=[None,.5],active_count=[None,2]))
    result=copy_labels(original,decisions)
    assert result['action'].to_list()==['WAIT','WAIT','WAIT','ENTRY','EXIT','WAIT']
    assert result['action_1a'].to_list()==original['action'].to_list()
    assert result['entry_gain'].to_list()==original['entry_gain'].to_list()
    assert result['allocation_loss_mask'].to_list()==[False,False,False,True,False,False]
    assert result['allocation_ratio'].to_list()==[0,0,0,.4,0,0]
    assert result['teacher_probabilities'].to_list()[0]==[0,1,0,0]
    assert all(abs(sum(p)-1)<1e-12 for p in result['teacher_probabilities'].to_list())
    assert original['action'].to_list()==['ENTRY','HOLD','EXIT','ENTRY','EXIT','WAIT']
