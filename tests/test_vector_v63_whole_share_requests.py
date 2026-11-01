import numpy as np
import pytest
from research.vectorized_backtest.v63.compact_execution import advance,state


def replay(signals,*,price=10.,ratio=1.,liquidity=None,cuts=()):
    n=len(signals);slots=np.tile(np.arange(5),(n,1)).astype(np.int32)
    signals=np.asarray([[v,0,0,0,0] for v in signals],np.int16)[:,None]
    ratios=np.full((n,1,5,3),ratio)
    prices=np.tile([price,price,price*.9,price*1.2,price*.95],(n,1,5,1))
    quotes=np.tile([price,price,price,1.,10000.,10000.,1.],(n,5,1))
    quotes[:,:,3]=np.arange(n)[:,None]+1
    if liquidity is not None:quotes[:,:,4:6]=np.asarray(liquidity)[:,None,None]
    buffers=state(1,5);records=[];episodes=[]
    start=0
    for end in (*cuts,n):
        rs,es=advance(start,slots[start:end],signals[start:end],ratios[start:end],prices[start:end],
            np.zeros((end-start,1,5),np.int64),np.zeros((end-start,1,5),bool),quotes[start:end],
            *buffers,1000.,200.,0.,0.,0.,99999)
        records.extend(rs[0]);episodes.extend(es[0]);start=end
    return buffers,records,episodes


@pytest.mark.parametrize('ratio',[0.,.01,.75,1.])
def test_entry_is_fixed_dollars_and_whole_shares_independent_of_network_size(ratio):
    _,orders,_=replay([1,2],price=3.,ratio=ratio)
    assert orders[0][2]==orders[0][3]==333
    assert orders[0][4]==999.


def test_zero_share_entry_and_add_are_never_submitted():
    buffers,orders,_=replay([1,0],price=1001.)
    assert not orders and buffers[-1][0,0]==0 and buffers[-1][0,1]==0
    assert buffers[-1][0,14]==1
    buffers,orders,_=replay([1,4,2],price=250.)
    assert [r[1] for r in orders]==[1,4]
    assert buffers[-1][0,1]==0 and buffers[-1][0,14]==1


def test_positive_reduce_closes_a_single_whole_share():
    buffers,orders,_=replay([1,8,2],price=900.,ratio=.25)
    assert [r[1] for r in orders]==[1,3]
    assert orders[1][3]==1
    assert buffers[-1][0,1]==0 and buffers[-1][0,14]==0


def test_suppression_causes_reconcile_and_zero_ratio_stays_noop():
    buffers,_,_=replay([1,8,2],price=900.,ratio=0.)
    d=buffers[-1][0]
    assert d[19]==1 and d[14]==sum(d[16:20])
    buffers,_,_=replay([1,4,2],price=250.)
    d=buffers[-1][0]
    assert d[18]==1 and d[14]==sum(d[16:20])


def test_five_filled_adds_reset_only_after_close_and_survive_block_boundaries():
    signals=[1]+[4]*6+[2,0,0,1]+[4]*6+[2]
    a,orders,episodes=replay(signals,cuts=(3,12))
    b,other,other_episodes=replay(signals)
    for x,y in zip(a,b):np.testing.assert_array_equal(x,y)
    assert orders==other and episodes==other_episodes
    assert sum(r[1]==2 for r in orders)==10 and len(episodes)==2
    assert a[-1][0,1]==0  # Suppressed adds were never submitted/penalized.
    assert a[-1][0,13]==2
    assert a[0][0,0,14]==0


def test_partial_add_counts_once_and_fractional_quote_size_never_fills_fraction():
    buffers,orders,_=replay([1]+[4]*7+[2],liquidity=[10000.]+[3.5]*7+[10000.])
    adds=[r for r in orders if r[1]==2]
    assert len(adds)==1 and adds[0][3]==20 and buffers[-2][0,5]==1
    assert all(r[2]==int(r[2]) and r[3]==int(r[3]) for r in orders)


def test_reduction_rounds_down_and_exit_clears_integer_remainder():
    buffers,orders,_=replay([1,8,2],price=3.,ratio=.25)
    assert [r[3] for r in orders]==[333,83,250]
    assert buffers[0][0,0,0]==0


def test_flat_management_signals_are_not_orders_or_penalties():
    buffers,orders,episodes=replay([2,8,10]*20,cuts=(7,31))
    assert not orders and not episodes
    assert buffers[-1][0,0]==buffers[-1][0,1]==0


def test_flat_exit_does_not_cancel_pending_entry_and_held_exit_still_works():
    buffers,orders,episodes=replay([1,2,0,2],liquidity=[.5,.5,10000.,10000.])
    assert [r[1] for r in orders]==[1,4]
    assert orders[0][3]==100 and len(episodes)==1
    assert buffers[-1][0,1]==1  # Actual liquidity block still charged once.


def test_simultaneous_flat_entry_and_exit_allows_entry_then_held_exit():
    buffers,orders,episodes=replay([3,2,2,8])
    assert [r[1] for r in orders]==[1,4]
    assert len(episodes)==1 and buffers[-1][0,1]==0


@pytest.mark.parametrize('ratio',[0.,.01,.75,1.])
def test_add_is_fixed_200_dollars_independent_of_network_size(ratio):
    _,orders,_=replay([1,4,2],price=3.,ratio=ratio)
    assert orders[1][1]==2 and orders[1][2]==orders[1][3]==66
    assert orders[1][4]==198.


def test_sub_share_liquidity_is_blocked_once_without_fractional_fill():
    buffers,orders,_=replay([1,0,0,2],liquidity=[.5,.5,10000.,10000.])
    assert buffers[-1][0,1]==1 and buffers[-1][0,6]==1
    assert orders[0][3]==100 and orders[0][7]==2


def test_authority_canonicalizes_add_cap_and_execution_rejects_old_contract():
    from dataclasses import replace
    from research.vectorized_backtest.v63.authority import feasible_policy
    from research.vectorized_backtest.v63.policy import Policy
    from research.vectorized_backtest.v63.evaluator import Execution
    assert feasible_policy(replace(Policy(),maximum_adds=20)).maximum_adds==5
    with pytest.raises(ValueError):Execution(entry_dollars=500).validate()
    with pytest.raises(ValueError):Execution(add_dollars=100).validate()
    with pytest.raises(ValueError):Execution(order_mode='explicit-top5-limit-v1').validate()
    assert Execution().validate().entry_dollars==1000
