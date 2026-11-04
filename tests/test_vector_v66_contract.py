from dataclasses import replace
import numpy as np
import pytest
from research.vectorized_backtest.v66.compact_execution import advance,state
from research.vectorized_backtest.v66.genome import Network,Neuron,INPUTS
from research.vectorized_backtest.v66.authority import ALLOWED
from research.vectorized_backtest.v66.canonical import network
from research.vectorized_backtest.v66.price_program import entry_guard


def test_random_parent_schedule_covers_crossover_and_mutation_independently():
    from research.vectorized_backtest.v66.lineage import exploration_slots
    from research.vectorized_backtest.v66.schedule import balanced
    for generation in (1,2,40,300):
        mask=exploration_slots(717,generation,np.random.default_rng(51))
        repeat=exploration_slots(717,generation,np.random.default_rng(51))
        growth=balanced(717,[.2,.8],generation)==0
        np.testing.assert_array_equal(mask,repeat)
        assert abs(mask.sum()-717*.2)<=1
        assert (mask&growth).any() and (mask&~growth).any()
        assert (~mask&growth).any() and (~mask&~growth).any()


def test_product_rejects_overflow_before_a_later_zero_operand():
    from research.vectorized_backtest.v66.canonical import interval
    node=Neuron((INPUTS,INPUTS+1,INPUTS+2),(1.,1.,1.),0.,op='product')
    with pytest.raises(ValueError,match='product intermediate'):
        interval(node,[(1e20,1e20),(1e20,1e20),(0.,0.)])


def test_operators_return_canonical_children_before_identity_and_genealogy():
    from research.vectorized_backtest.v66.genome import sample
    from research.vectorized_backtest.v66.authority import construct
    from research.vectorized_backtest.v66.composition import mutation,cross
    rng=np.random.default_rng(62)
    entry=Network((Neuron((ALLOWED[0],),(1.,),0.,op='identity'),))
    parents=[construct(replace(m,networks=dict(m.networks,entry=entry))) for m in sample(rng,2)]
    accepted={mutation:0,cross:0}
    for operator in accepted:
        for _ in range(16):
            try:child,meta=operator(rng,parents[0],1 if operator is mutation else parents[1])
            except (ValueError,RuntimeError):continue
            assert child.payload()==construct(child).payload()
            accepted[operator]+=1
        assert accepted[operator]>0


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
    # AND/OR cannot become decision: decision zeroes an unknown operand,
    # while Boolean bridges expose its value to downstream operators.
    assert got.nodes[-1].op=='whole_or' and got.nodes[-1].weights==(1.,1.) and got.nodes[-1].bias==0
    assert network(got,'entry')==got


def test_impossible_entries_rejected_disabled_management_compacted():
    net=Network((Neuron((ALLOWED[0],),(.01,),0.,op='tanh'),),threshold=.8)
    with pytest.raises(ValueError,match='impossible entry'):network(net,'entry')
    got=network(net,'exit');assert len(got.nodes)==1 and got.nodes[0].weights==(0.,)
    with pytest.raises(ValueError):network(replace(net,nodes=(replace(net.nodes[0],weights=(float('nan'),)),)),'exit')


def test_sharing_exposes_impossible_head_in_one_construction():
    x=ALLOWED[0]
    net=Network((Neuron((x,),(1.,),0.,op='identity'),
                 Neuron((x,),(1.,),0.,op='identity'),
                 Neuron((INPUTS,INPUTS+1),(1.,1.),0.,op='whole_xor')),threshold=.1)
    with pytest.raises(ValueError,match='impossible entry'):network(net,'entry')
    got=network(net,'exit')
    assert len(got.nodes)==1 and network(got,'exit')==got


def test_canonical_graph_gpu_actions_and_reduce_size_are_exact():
    import torch
    if not torch.cuda.is_available():pytest.skip('CUDA required')
    from research.vectorized_backtest.v66.runtime import configure_caches
    from research.vectorized_backtest.v66.genome import sample
    from research.vectorized_backtest.v66.network import NetworkPlan
    configure_caches('D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v66/unit-compiler')
    from research.vectorized_backtest.v66.network_cuda import market_batch
    x,y=ALLOWED[:2];base=sample(np.random.default_rng(218),1)[0];members=[]
    for op in ('whole_and','whole_or','whole_xor','whole_xnor'):
        # Unknown biased operands and a known sibling expose mask-changing rewrites.
        net=Network((Neuron((x,),(1.,),.7,op='identity'),Neuron((x,),(1.,),.7,op='identity'),
                     Neuron((INPUTS,INPUTS+1),(0.,-.3),.2,op=op),
                     Neuron((y,),(1.,),0.,op='identity'),
                     Neuron((INPUTS+2,INPUTS+3),(1.,1.),0.,op='whole_or')),size_source=2)
        raw=replace(base,networks={s:net for s in base.networks}).validate()
        fixed=replace(raw,networks={s:network(net,s) for s in raw.networks}).validate()
        members.extend((raw,fixed))
    plan=NetworkPlan(members,'cuda');plan.prepare(len(members),5)
    try:
        t=17;c=len(plan.columns);torch.manual_seed(217)
        values=torch.rand((t,5,3*c),device='cuda')*2-1
        valid=torch.rand((t,5,c),device='cuda')>.5
        work=torch.empty((t,plan.total_nodes,5),device='cuda');known=torch.empty_like(work,dtype=torch.bool)
        signals,ratios=market_batch(plan,values,valid,torch.ones((t,5),device='cuda',dtype=torch.bool),work,known,
                                   torch.arange(5,device='cuda',dtype=torch.int32)[None].expand(t,-1))
        torch.testing.assert_close(signals[:,::2],signals[:,1::2],rtol=0,atol=0)
        torch.testing.assert_close(ratios[:,::2,...,2],ratios[:,1::2,...,2],rtol=0,atol=0)
    finally:plan.close()
