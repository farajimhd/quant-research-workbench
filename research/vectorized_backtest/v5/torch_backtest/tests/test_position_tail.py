import numpy as np
from research.vectorized_backtest.v5.torch_backtest.position_tail import episode_pnls,tail_summary


def fill(stamp,ticker,side,qty,price,fee=0):return [stamp,ticker,0,side,qty,price,fee,0,0]


def test_episode_handles_adds_partial_reductions_and_interleaved_tickers():
    rows=[fill(1,0,1,10,2,1),fill(2,1,1,2,10),fill(3,0,1,5,3,1),
        fill(4,0,-1,8,4,1),fill(5,1,-1,2,9),fill(6,0,-1,7,5,1)]
    np.testing.assert_array_equal(episode_pnls(rows),[28.,-2.])
    assert tail_summary(episode_pnls(rows))['position_tail_mean_pnl']==-2.


def test_partial_fill_splitting_does_not_change_episode_penalty():
    whole=[fill(1,0,1,10,2),fill(2,0,-1,10,3,1)]
    partial=[fill(1,0,1,10,2),fill(2,0,-1,4,3,.4),fill(2,0,-1,6,3,.6)]
    np.testing.assert_array_equal(episode_pnls(whole),episode_pnls(partial))
    assert tail_summary([])['position_tail_mean_pnl'] is None


def test_open_episode_is_excluded_and_new_episode_restarts():
    rows=[fill(1,0,1,5,2),fill(2,0,-1,5,3),fill(3,0,1,2,4),fill(4,0,-1,1,5)]
    np.testing.assert_array_equal(episode_pnls(rows),[5.])
