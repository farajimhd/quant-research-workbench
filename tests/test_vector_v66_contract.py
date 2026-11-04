from dataclasses import replace
import numpy as np
import pytest
from research.vectorized_backtest.v66.compact_execution import advance,state
from research.vectorized_backtest.v66.genome import Network,Neuron,INPUTS
from research.vectorized_backtest.v66.authority import ALLOWED
from research.vectorized_backtest.v66.canonical import network
from research.vectorized_backtest.v66.price_program import entry_guard


def run(sig,owners,quotes,prices,cuts=()):
    n=len(sig);slots=np.full((n,5),-1,np.int32);slots[:,0]=0
    signals=np.zeros((n,2,5),np.int16);signals[:,:,0]=sig
    selected=np.zeros((n,1,5),np.int64);selected[:,0,0]=owners
    packet=np.tile([10.,10.,9.,12.,9.5],(n,2,5,1));packet[:,:,0]=prices
    book=np.tile([10.,10.,10.,0.,1000.,1000.,1.],(n,1,1));book[:,0]=quotes
    buffers=state(1,1);orders=[];episodes=[];start=0
    for end in (*cuts,n):
        a,b=advance(start,slots[start:end],signals[start:end],np.ones((end-start,2,5,3)),packet[start:end],selected[start:end],
            np.zeros((end-start,2,5),bool),book[start:end],*buffers,1000.,200.,0.,0.,0.,999)
        orders.extend(a[0]);episodes.extend(b[0]);start=end
    return buffers,orders,episodes


def test_owner_ignores_new_entry_route_and_survives_partial_fill_and_chunks():
    # First branch enters partially; route flips but second branch's exit must
    # not close the position. Original owner's exit closes it on clock three.
    sig=[[1,0],[0,2],[0,2],[2,0]]
    quote=[[10,10,10,i+1,1000,50,1] for i in range(4)]
    price=np.tile([10,10,9,12,9.5],(4,2,1))
    a,orders,episodes=run(sig,[0,1,1,1],quote,price,cuts=(1,3))
    b,other,closed=run(sig,[0,1,1,1],quote,price)
    assert orders==other and episodes==closed
    for x,y in zip(a,b):np.testing.assert_array_equal(x,y)
    assert len(episodes)==1 and episodes[0][2]==0 and episodes[0][4]==3
    assert orders[0][3]==100


def test_spread_blocks_initial_and_residual_fills_but_never_exit():
    sig=[[1,0],[1,0],[0,0],[2,0]]
    quote=[[10,10,10.6,1,1000,1000,1],[10,10,10.4,2,1000,20,1],
           [10,10,10.6,3,1000,1000,1],[10,10,10.6,4,1000,1000,1]]
    price=np.tile([11,10,9,12,9.5],(4,2,1))
    buffers,orders,episodes=run(sig,[0]*4,quote,price)
    assert orders[0][3]==20 and orders[-1][1]==4 and orders[-1][3]==20
    assert len(episodes)==1 and buffers[-1][0,22]>=2


def test_guard_is_bid_relative_and_has_no_dollar_spread_cap():
    assert entry_guard(105.,99.,120.,105.,100.)
    assert not entry_guard(106.,99.,120.,105.01,100.)
    assert entry_guard(10.,9.97,12.,10.,10.)
    assert not entry_guard(10.,9.98,12.,10.,10.)
    assert not entry_guard(34.22,8.53,40.,34.22,8.56)


def test_exact_expression_sharing_and_ignored_bridge_genes():
    x=ALLOWED[0]
    net=Network((Neuron((x,),(1.,),0.,op='identity'),Neuron((INPUTS,),(1.,),0.,op='tanh'),
                 Neuron((x,),(1.,),0.,op='identity'),Neuron((INPUTS+2,),(1.,),0.,op='tanh'),
                 Neuron((INPUTS+1,INPUTS+3),(.3,-.6),.2,op='whole_or')))
    got=network(net,'entry')
    assert len(got.nodes)<len(net.nodes)
    assert got.nodes[-1].op=='decision' and got.nodes[-1].weights==(.5,) and got.nodes[-1].bias==0
    assert network(got,'entry')==got


def test_impossible_entries_rejected_disabled_management_compacted():
    net=Network((Neuron((ALLOWED[0],),(.01,),0.,op='tanh'),),threshold=.8)
    with pytest.raises(ValueError,match='impossible entry'):network(net,'entry')
    got=network(net,'exit');assert len(got.nodes)==1 and got.nodes[0].weights==(0.,)
    with pytest.raises(ValueError):network(replace(net,nodes=(replace(net.nodes[0],weights=(float('nan'),)),)),'exit')
