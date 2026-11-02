import numpy as np
from research.vectorized_backtest.v65.attention import selected_columns,execution_columns
from research.vectorized_backtest.v65.compact_execution import advance,state
from research.vectorized_backtest.v65.request_penalty import assess
from research.vectorized_backtest.v65.price_program import Prices,Expression


def run(slots,signals,*,sizes=None,prices=None):
    slots=np.asarray(slots,np.int32);n=len(slots);u=6
    signals=np.asarray(signals,np.int16).reshape(n,1,5)
    quotes=np.zeros((n,u,7));quotes[:,:,:3]=10.;quotes[:,:,3]=np.arange(1,n+1)[:,None]
    quotes[:,:,4:6]=1000.;quotes[:,:,6]=1
    if sizes is not None:quotes[:,:,4:6]=np.asarray(sizes)[:,None,None]
    pp=np.zeros((n,1,5,5));pp[:]=[10.,10.,9.,12.,9.5]
    if prices is not None:pp[:]=prices
    buffers=state(1,u)
    rows,closed=advance(0,slots,signals,np.ones((n,1,5,3)),pp,np.zeros((n,1,5),np.int64),
        np.zeros((n,1,5),bool),quotes,*buffers,100.,20.,.005,1.,0.,10000)
    return buffers,rows,closed


def test_rank_order_is_preserved():
    saved=np.array([[9,3,7,1,5,0,2,4,6,8]])
    assert selected_columns(saved,np.arange(10)).tolist()==[[9,3,7,1,5]]


def test_union_keeps_obligations():
    q=np.zeros((2,8));o=q.copy();q[1,7]=1;o[0,6]=1
    assert execution_columns([0,1,2,3,4],q,o).tolist()==[0,1,2,3,4,6,7]


def test_rank_loss_closes_position():
    buffers,orders,episodes=run([[0,1,2,3,4],[5,1,2,3,4]],[[1,0,0,0,0],[0,0,0,0,0]])
    assert buffers[0][0,0,0]==0
    assert len(episodes[0])==1
    assert episodes[0][0][0]==-2.
    assert buffers[-1][0,9]==1
    assert buffers[-1][0,1]==0


def test_rank_loss_partial_exit_continues():
    buffers,orders,episodes=run([[0,1,2,3,4],[5,1,2,3,4],[5,1,2,3,4],[5,1,2,3,4]],
        [[1,0,0,0,0],[0]*5,[0]*5,[0]*5],sizes=[100,4,4,4])
    assert buffers[0][0,0,0]==0
    assert len(episodes[0])==1
    assert buffers[-1][0,9]==1
    assert buffers[-1][0,1]==0


def test_wait_penalty_is_once():
    buffers,orders,episodes=run([[0,1,2,3,4]]*3,[[1,0,0,0,0]]*3,sizes=[0,0,0])
    assert buffers[-1][0,1]==1
    assert buffers[0][0,0,13]==1


def test_invalid_initial_stop_is_not_replaced_and_is_penalized():
    buffers,orders,episodes=run([[0,1,2,3,4]],[[1,0,0,0,0]],prices=[10.,10.,11.,12.,9.5])
    assert buffers[0][0,0,0]==0 and not episodes[0]
    assert buffers[-1][0,1]==buffers[-1][0,3]==1


def test_reentry_waits_three_seconds_after_full_exit():
    buffers,orders,episodes=run([[0,1,2,3,4]]*5,
        [[1,0,0,0,0],[2,0,0,0,0],[1,0,0,0,0],[1,0,0,0,0],[1,0,0,0,0]])
    buys=[r for r in orders[0] if r[1]==1]
    assert [r[6] for r in buys]==[0.,4.]
    assert len(episodes[0])==1


def test_quote_liquidity_is_consumed_once_and_sells_are_inventory_bounded():
    n=4;slots=np.tile(np.arange(5),(n,1)).astype(np.int32)
    signals=np.zeros((n,1,5),np.int16);signals[0,0,0]=1;signals[2:,0,0]=8
    quotes=np.tile([10.,10.,10.,1.,3.,3.,1.],(n,5,1))
    quotes[3,:,3]=2  # No new displayed capacity during the first three clocks.
    ratios=np.ones((n,1,5,3));ratios[:,:,0,2]=10.
    prices=np.tile([10.,10.,9.,12.,9.5],(n,1,5,1));buffers=state(1,5)
    records,episodes=advance(0,slots,signals,ratios,prices,np.zeros((n,1,5),np.int64),
        np.zeros((n,1,5),bool),quotes,*buffers,100.,20.,0.,0.,0.,999)
    assert buffers[0][0,0,0]==0
    assert sum(r[3] for r in records[0] if r[1] in (3,4))==3.
    assert len(episodes[0])==1


def test_duplicate_reasons_one_penalty():
    reasons=np.ones((2,6),bool)
    charged,cost=assess(reasons,np.array([False,True]))
    assert cost.tolist()==[5.,0.]
    assert charged.all()


def test_no_implicit_stop_fallback():
    program=Prices(stop=Expression('swing.low.3.30'))
    values={'ask':np.array([10.]),'bid':np.array([9.9]),'close':np.array([10.]),'swing.low.3.30':np.array([np.nan])}
    prices,guard=program.evaluate(values)
    assert not guard[0] and np.isnan(prices[2][0])


def test_live_panel_does_not_regress_to_founder_generation():
    from research.vectorized_backtest.v65.progress_authority import LiveStatus
    authority=LiveStatus();owner=dict(pid=1,creation_time=1.)
    assert authority.accept(dict(owner=owner,generation=24,completed_generations=23,updated_epoch=10))['generation']==24
    assert authority.accept(dict(owner=owner,generation=1,completed_generations=1,updated_epoch=11))['generation']==24
    assert authority.accept({})['generation']==24
    assert authority.accept(dict(owner=dict(pid=2,creation_time=2.),generation=1,updated_epoch=12))['generation']==1


def test_cross_section_normalization_uses_five_only():
    import torch
    from research.vectorized_backtest.v65.feature_layout import column_sum
    values=torch.arange(30,dtype=torch.float32).reshape(2,5,3)
    assert torch.equal(column_sum(values,torch.tensor([1,400,800])),values.sum(1))


def test_compiled_execution_matches_scalar_oracle():
    from research.vectorized_backtest.v65.compact_execution import _one
    n=12;slots=np.tile(np.arange(5),(n,1)).astype(np.int32);slots[5:,0]=5
    signals=np.zeros((n,2,5),np.int16);signals[:5,:,0]=1
    ratios=np.ones((n,2,5,3));prices=np.tile([10.,10.,9.,12.,9.5],(n,2,5,1))
    owners=np.zeros((n,2,5),np.int64);abstain=np.zeros((n,2,5),bool)
    quotes=np.tile([10.,10.,10.,1.,3.,3.,1.],(n,6,1));quotes[:,:,3]=np.arange(n)[:,None]+1
    a=state(2,6);b=tuple(x.copy() for x in a)
    views=tuple(quotes[:,:,i].astype(bool) if i==6 else quotes[:,:,i] for i in range(7))
    compiled=advance(0,slots,signals,ratios,prices,owners,abstain,views,*a,100.,20.,.005,1.,0.,n-1)
    oracle=[_one.py_func(i,0,slots,signals,ratios,prices,owners,abstain,quotes,*b,100.,20.,.005,1.,0.,n-1) for i in range(2)]
    for x,y in zip(a,b):np.testing.assert_array_equal(x,y)
    for i in range(2):assert compiled[0][i]==oracle[i][0] and compiled[1][i]==oracle[i][1]


def test_price_plan_vectorization():
    import torch
    from types import SimpleNamespace
    from research.vectorized_backtest.v65.price_references import PricePlan
    from research.vectorized_backtest.v65.policy import Policy
    p=Prices().payload();members=[SimpleNamespace(policy=Policy(price_program=p)) for _ in range(2)]
    data=SimpleNamespace(device=torch.device('cpu'),host_tensors={k:torch.full((3,6),10.) for k in ('mark','ask','bid')})
    slots=np.tile(np.arange(5),(3,1));plan=PricePlan(members,data,slots)
    values,guard=plan(torch.arange(3))
    assert values.shape==(3,2,5,5)
    assert guard.all()
    np.testing.assert_allclose(values[0,0,0].numpy(),[10,10,9.7,10.5,9.7])


def test_score_penalty_is_not_doubled_by_aggregation():
    from research.vectorized_backtest.v65.robust_score import combine
    panel={k:np.array([0.]) for k in ('net_pnl','gross_losses','drawdown','closed_positions','winning_positions','ticker_component','maximum_position_risk')}
    panel.update(blocked_requests=np.array([3.]),eligible=np.array([True]))
    local=combine([panel])
    assert local['score'][0]==-15.
    assert combine([local,local])['score'][0]==-30.


def test_candle_extremes_use_existing_observations_and_completed_windows():
    import torch
    from types import SimpleNamespace
    from research.vectorized_backtest.v65.price_references import References
    clocks=np.arange(10);source=np.arange(10)[:,None]
    observed=np.ones((10,1),bool);observed[2]=False
    data=SimpleNamespace(arrays={'clocks':clocks},bank={'source_rows':source},
         host_tensors={'observed':torch.from_numpy(observed)},market={'low':np.arange(10,dtype=float)+1,'high':np.arange(10,dtype=float)+2})
    refs=References(data,np.zeros((10,5),np.int32))
    low=refs.values('candle.low.5')
    assert np.isnan(low[:4]).all()
    assert (low[4:9]==1.).all()
    assert (low[9]==6.).all()
    assert (refs.values('previous.low.5')[9]==1.).all()


def test_sparse_swing_matches_dense_reference_with_missing_bars_and_rank_changes():
    from research.vectorized_backtest.v65.price_references import confirmed_selected
    rng=np.random.default_rng(47)
    source=rng.integers(1,10,(140,9)).astype(float)
    source[rng.random(source.shape)<.12]=np.nan
    slots=np.stack([rng.choice(9,5,replace=False) for _ in range(140)])
    slots[0,0]=-1
    rows,columns=np.nonzero(np.isfinite(source))
    for left,right in ((1,1),(3,4),(5,10),(60,60)):
        width=left+right+1
        for low in (True,False):
            expected=np.full_like(source,np.nan)
            for col in range(9):
                x=source[:,col];windows=np.lib.stride_tricks.sliding_window_view(x,width)
                extreme=(np.min if low else np.max)(windows,axis=1)
                ok=np.isfinite(windows).all(1)&(windows[:,left]==extreme)
                at=np.arange(width-1,len(x));confirmed=np.full(len(x),-1)
                confirmed[at[ok]]=at[ok]-right
                prior=np.r_[-1,np.maximum.accumulate(confirmed)[:-1]]
                expected[:,col]=np.where(prior>=0,x[prior.clip(0)],np.nan)
            result=confirmed_selected(source,slots,rows,columns,left,right,low)
            wanted=expected[np.arange(len(slots))[:,None],slots.clip(0)]
            wanted[slots<0]=np.nan
            np.testing.assert_array_equal(result,wanted)


def test_scoped_identity_matches_canonical_identity():
    from research.vectorized_backtest.v65.genome import sample
    from research.vectorized_backtest.v65.identity_cache import identity_scope,program_key
    from research.vectorized_backtest.v65.identity import digest
    member=sample(np.random.default_rng(7),1)[0]
    payload=member.payload()
    for network in payload['networks'].values():
        for node in network['nodes']:node.pop('uid',None);node.pop('slot',None)
    with identity_scope():assert program_key(member)==digest(payload)


def test_fused_price_kernel_matches_torch_exactly():
    import torch,pytest
    from types import SimpleNamespace
    if not torch.cuda.is_available():pytest.skip('CUDA required')
    from research.vectorized_backtest.v65.runtime import configure_caches
    configure_caches('D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v65/unit-compiler')
    from research.vectorized_backtest.v65.price_cuda import evaluate
    generator=torch.Generator().manual_seed(12)
    bank=torch.rand((11,5,7),generator=generator,dtype=torch.float64)*50
    bank[3,1,0]=float('nan');bank[2,4,1]=float('inf')
    a=torch.randint(7,(9,5),generator=generator);b=torch.randint(7,(9,5),generator=generator)
    op=torch.arange(5).expand(9,-1).contiguous()
    w=torch.rand((9,5),generator=generator);offset=torch.rand((9,5),generator=generator)*.3
    vol=torch.rand((9,5),generator=generator);indices=torch.tensor([0,2,3,7,10])
    sampled=bank[indices];av=sampled[:,:,a].permute(0,2,1,3);bv=sampled[:,:,b].permute(0,2,1,3)
    code=op[None,:,None];weight=w[None,:,None]
    expected=torch.where(code==0,av,torch.where(code==1,torch.minimum(av,bv),torch.where(code==2,torch.maximum(av,bv),
        torch.where(code==3,weight*av+(1-weight)*bv,torch.where(torch.isfinite(av)&(av>0),av,bv)))))
    expected=expected*(1+offset[None,:,None])+sampled[:,:,6][:,None,:,None]*vol[None,:,None]
    plan=SimpleNamespace(bank=bank.cuda(),a=a.cuda(),b=b.cuda(),op=op.cuda(),weight=w.cuda(),offset=offset.cuda(),volatility=vol.cuda(),vol_index=6,names=list(range(7)))
    actual=evaluate(plan,indices.cuda()).cpu()
    torch.testing.assert_close(actual,expected,rtol=0,atol=0,equal_nan=True)


def test_lag_reuse_follows_ticker_identity_across_rank_changes(monkeypatch):
    import torch
    from types import SimpleNamespace
    from research.vectorized_backtest.v65 import compact_inference as module
    calls=[]
    def projection(plan,data,indices,absolute_only=False,slots=None):
        c=len(plan.columns);x=(indices[:,None]*100+slots).float()[...,None].expand(-1,-1,c)
        calls.append((absolute_only,x.shape[0]*x.shape[1]))
        return (x if absolute_only else torch.cat((x,x*0,torch.ones_like(x)),-1)),torch.ones_like(x,dtype=torch.bool)
    monkeypatch.setattr(module,'project',projection)
    engine=module.Inference.__new__(module.Inference)
    engine.base=SimpleNamespace(columns=[0,1],uses_cross=True,cross_indices=torch.tensor([0]))
    engine.lag_projection=SimpleNamespace(columns=[0]);engine.data=None
    engine.slots=torch.tensor([[0,1,2,3,4],[4,0,1,2,3],[5,4,0,1,2],[5,4,0,1,2]])
    indices=torch.arange(4)
    values,valid,slots=engine.materialize(indices)
    expected=(indices-1).clamp_min(0)[:,None]*100+slots
    torch.testing.assert_close(values[:,:,-1],expected.float(),rtol=0,atol=0)
    assert not valid[0,:,-1].any() and valid[1:,:,-1].all()
    assert calls==[(False,20),(True,6)]  # Five boundary values and one newcomer.


def test_time_tiled_graph_matches_original_kernel_exactly():
    import torch,pytest
    from dataclasses import replace
    if not torch.cuda.is_available():pytest.skip('CUDA required')
    from research.vectorized_backtest.v65.runtime import configure_caches
    configure_caches('D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v65/unit-compiler')
    from research.vectorized_backtest.v65.genome import sample,random_network
    from research.vectorized_backtest.v65.network import NetworkPlan
    from research.vectorized_backtest.v65.network_cuda import market_batch
    rng=np.random.default_rng(825);members=sample(rng,24)
    for i in range(8):
        members[i]=replace(members[i],networks={s:random_network(rng,s,7) for s in members[i].networks}).validate()
    members.extend(members[:2])  # Also exercise shared graph representatives.
    plan=NetworkPlan(members,'cuda');plan.prepare(len(members),5)
    try:
        for t in (1,17,33):
            c=len(plan.columns);extra=len(plan.cross_columns) if plan.uses_cross else 0
            torch.manual_seed(71)
            values=torch.rand((t,5,3*c+extra),device='cuda')*2-1
            valid=torch.rand((t,5,c+extra),device='cuda')>.1
            membership=torch.rand((t,5),device='cuda')>.2
            work=torch.empty((t,plan.total_nodes,5),device='cuda');known=torch.empty_like(work,dtype=torch.bool)
            entries=torch.arange(5,device='cuda',dtype=torch.int32)[None].expand(t,-1)
            plan.cuda_time_tiled=False;a=market_batch(plan,values,valid,membership,work,known,entries)
            plan.cuda_time_tiled=True;b=market_batch(plan,values,valid,membership,work,known,entries)
            for x,y in zip(a,b):torch.testing.assert_close(x,y,rtol=0,atol=0)
    finally:plan.close()


def test_candidate_detail_keeps_sealed_validation_while_live_is_partial():
    from pathlib import Path
    from research.vectorized_backtest.v65.campaign_progress import Model
    model=Model.__new__(Model)
    model.selected=dict(serial=7,generation=24);model.day=None;model.aggregate_mode=False
    model.report=lambda:dict(generation=24);model.target=lambda:Path('unused')
    model.read=lambda path:{}
    panel={name:[1.] for name in ('score','net_pnl','gross_losses','drawdown')}
    def snapshot(sessions):
        return dict(generation=24,panels={s:dict(ids=[7],panel=panel) for s in sessions})
    model.live_data=snapshot(['training:a'])
    model.global_data=snapshot(['training:a','training:b','validation:c'])
    assert [r['session'] for r in model.candidate_sessions()]==['training:a','training:b','validation:c']


def test_observer_summary_preserves_request_penalty_fields():
    from research.vectorized_backtest.v65.evolution_report import summarize
    panel={k:[0.] for k in ('score','net_pnl','gross_losses','drawdown','closed_positions')}
    panel.update(eligible=[True],blocked_requests=[3],blocked_penalty=[15.])
    row=summarize([7],panel,[dict(serial=7)])['candidates'][0]
    assert row['blocked_requests']==3 and row['blocked_penalty']==15.
