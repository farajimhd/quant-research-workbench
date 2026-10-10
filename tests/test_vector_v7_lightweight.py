from dataclasses import replace
from types import SimpleNamespace
import json
import numpy as np
import polars as pl
import pytest
import torch
from research.vectorized_backtest.v6.torch_backtest.program import Program,Node,Op
from research.vectorized_backtest.v6.torch_backtest.compact_prepare import VERSION,KEY_STRIDE
from research.vectorized_backtest.v6.torch_backtest.runtime import file_hash
from research.vectorized_backtest.v6.torch_backtest.materialize_history import prepare
from research.vectorized_backtest.v7.genome import Individual,Policy,STAGES,sample,mutate
from research.vectorized_backtest.v7.features import BASE,CATALOG,LIQUIDITY
from research.vectorized_backtest.v7.data import SessionData
from research.vectorized_backtest.v7.evaluator import PopulationPrograms,Execution,replay_cohort,step


def member(**policy):
    rules={s:Program((Node(Op.CONSTANT,value=int(s=='entry'),unit='bool'),),0) for s in STAGES}
    return Individual(rules,Policy(**policy)).validate()


def session(prices):
    x=torch.tensor(prices,dtype=torch.float64)[:,None]
    return SimpleNamespace(device=torch.device('cpu'),clocks=len(prices),listing_ids=[0],
        tensors=dict(mark=x,observed=torch.ones_like(x,dtype=torch.bool),membership=torch.ones_like(x,dtype=torch.bool),
                     history_ids=torch.arange(len(prices))[:,None].to(torch.int32)),
        swing_bank=lambda members:(torch.full((len(prices),1),float('nan'),dtype=torch.float64),torch.zeros(len(members),dtype=torch.int64)))


def test_fixed_entry_uses_next_close_not_signal_close():
    x=session([10.,20.,22.]);gates=torch.ones((1,3,1),dtype=torch.uint8)
    result=replay_cohort([x],[member(target_fraction=.5)],[gates])[0]
    # Signal at 10 executes at 20: 50 shares, then liquidation at 22.
    assert result['net_pnl'].item()==100.
    assert result['filled_batches'].item()==1
    assert result['terminal_valid'].item()


def test_add_is_fixed_200_and_partial_reduction_has_correct_profit():
    x=session([10.,10.,11.,12.,13.]);gates=torch.tensor([1,4,8,0,0],dtype=torch.uint8)[None,:,None]
    result=replay_cohort([x],[member(cooldown=1,target_fraction=.9,add_minimum_profit=0.,reduce_minimum_profit=0.,reduce_fraction=.5)],[gates])[0]
    # Buy 100@$10, add 200/11@$11, half exit@$12, rest@$13.
    quantity=100+200/11;average=1200/quantity
    expected=quantity/2*(12-average)+quantity/2*(13-average)
    assert result['net_pnl'].item()==pytest.approx(expected)
    assert result['add_count'].item()==1
    assert result['reduce_count'].item()==1
    assert result['closed_positions'].item()==1
    assert result['worst_position_pnl'].item()==pytest.approx(expected)


def test_cohort_parallel_lanes_equal_separate_and_fees():
    data=[session([10,10,11]),session([20,20,18,19])]
    members=[member(target_fraction=.5),member(target_fraction=.5)]
    gates=[torch.ones((2,d.clocks,1),dtype=torch.uint8) for d in data]
    actual=replay_cohort(data,members,gates,execution=Execution(cost_bps=10))
    for i,item in enumerate(data):
        expected=replay_cohort([item],members,[gates[i]],execution=Execution(cost_bps=10))[0]
        for name in expected:torch.testing.assert_close(actual[i][name],expected[name],rtol=0,atol=0)


def test_scripted_position_kernel_matches_eager():
    scripted=torch.jit.script(step)
    position=torch.zeros((2,3,4,9),dtype=torch.float64);aggregate=torch.zeros((2,3,10),dtype=torch.float64);aggregate[...,8]=float('inf')
    price=torch.full((2,1,4),10.,dtype=torch.float64);boolean=torch.ones_like(price,dtype=torch.bool)
    policy=torch.tensor([.03,.05,.25,.03,.01,.02,2.,30.,0.],dtype=torch.float64)[None,None,None].expand(1,3,1,9)
    args=(position,aggregate,price,price,boolean,boolean,torch.ones((2,3,4),dtype=torch.uint8),
          price.expand(2,3,4),policy,torch.tensor(1.),torch.zeros((2,1,1),dtype=torch.bool),1000.,200.,0.)
    expected=step(*args);actual=scripted(*args)
    for a,b in zip(actual,expected):torch.testing.assert_close(a,b,rtol=0,atol=0)


def test_cost_basis_is_exact_on_entry_and_unchanged_without_purchase():
    position=torch.zeros((1,1,1,9),dtype=torch.float64);aggregate=torch.zeros((1,1,10),dtype=torch.float64);aggregate[...,8]=float('inf')
    price=torch.full((1,1,1),3.75,dtype=torch.float64);yes=torch.ones_like(price,dtype=torch.bool);no=torch.zeros_like(yes)
    policy=torch.tensor([.03,.9,.25,.03,0.,0.,2.,1.,0.],dtype=torch.float64)[None,None,None]
    args=(price,price,yes,yes,torch.ones((1,1,1),dtype=torch.uint8),price,policy,torch.tensor(1.),no,1000.,200.,0.)
    position,aggregate=step(position,aggregate,*args)
    assert position[...,1].item()==3.75
    # Deliberately awkward quantity used to make repeated q*avg/q rounding
    # observable. No trade and a partial sale must preserve basis bit for bit.
    position[...,0]=240.;position[...,1]=3.750000000000001
    for signal in (0,8):
        before=position[...,1].clone();previous=price+1
        position,aggregate=step(position,aggregate,price,previous,yes,yes,torch.full((1,1,1),signal,dtype=torch.uint8),
            price,policy,torch.tensor(10.),no,1000.,200.,0.)
        assert torch.equal(position[...,1],before)


@pytest.mark.parametrize('entry,add',[(1000.,200.),(1000.01,200.02)])
def test_purchase_fees_and_fractional_notionals_use_float64(entry,add):
    data=session([5.,5.,5.,5.]);gates=torch.tensor([1,4,0,0],dtype=torch.uint8)[None,:,None]
    result=replay_cohort([data],[member(cooldown=1,add_minimum_profit=0.)],[gates],
        execution=Execution(entry_dollars=entry,add_dollars=add,cost_bps=10.))[0]
    assert result['add_count'].item()==1
    assert result['net_pnl'].item()==pytest.approx(-2*(entry+add)*.001,rel=0,abs=1e-12)


def test_v7_programs_include_history_and_quote_features():
    rng=np.random.default_rng(23);rows=sample(rng,100)
    used={n.feature for v in rows for p in v.rules.values() for n in p.nodes if n.op==Op.FEATURE}
    assert any(i>=len(BASE) for i in used)
    assert any(i>=len(CATALOG)-len(LIQUIDITY) for i in used)
    for row in rows:mutate(rng,row).validate()


def fixture_files(tmp_path):
    root=tmp_path/'inputs';root.mkdir();n=80;clocks=np.arange(1000,1000+n,dtype=np.int64)
    keys=clocks.copy();features=np.zeros((n,len(BASE)),dtype=np.float32);features[:,3]=np.log(10.)
    valid=np.ones_like(features,dtype=bool)
    arrays=dict(clocks=clocks,top_indices=np.zeros((n,1),dtype=np.int32),market_keys=keys,feature_keys=keys,features=features,feature_valid=valid)
    for name,v in arrays.items():np.save(root/(name+'.npy'),v,allow_pickle=False)
    pl.DataFrame(dict(clock=clocks,listing=np.zeros(n,dtype=np.int32),source_row=np.arange(n),mark=np.full(n,10.),
        high=np.full(n,10.1),low=np.full(n,9.9),observed=np.ones(n,dtype=bool),notional=np.full(n,100.),
        feature_row=np.arange(n),bid=np.full(n,9.99),ask=np.full(n,10.01),quote_us=np.full(n,clocks[0]*1000000),
        volume=np.full(n,10.),trade_count=np.ones(n))).write_parquet(root/'market.parquet')
    names=[name+'.npy' for name in arrays]+['market.parquet']
    receipt=dict(identity=dict(version=VERSION,session=dict(day='synthetic')),ready_for_replay=True,validation_opened=False,
        files={name:file_hash(root/name) for name in names})
    (root/'complete.json').write_text(json.dumps(receipt))
    history=tmp_path/'history';prepare(root,history,workers=1)
    return root,history


def test_actual_materializer_and_v7_second_close_quote_freshness(tmp_path):
    root,history=fixture_files(tmp_path);data=SessionData(root,history)
    values,valid=data.feature_block(0,5,[0]);start=len(CATALOG)-len(LIQUIDITY)
    assert values.shape==(1,5,len(CATALOG))
    assert valid[0,:2,start:start+4].all()
    assert not valid[0,2:,start:start+3].any()
    assert valid[0,2:,start+3].all() and values[0,2,start+3]==2
    assert values[0,0,start+2].item()==pytest.approx(.002)
    # Chunked evaluation must equal whole-session evaluation, including temporal context.
    individual=member();shared=PopulationPrograms([individual])
    gates=shared.evaluate(data,chunk=17,listing_batch=1)
    assert gates.shape==(1,80,1) and ((gates&255)==1).all() and ((gates>>8)==1).all()
    expected=shared.evaluate(data,chunk=80,listing_batch=1)
    torch.testing.assert_close(gates,expected,rtol=0,atol=0)
    result=replay_cohort([data],[individual],[gates])[0]
    assert result['terminal_valid'].item() and result['net_pnl'].item()==0


def test_persisted_features_exact_slices_reuse_and_integrity(tmp_path):
    from research.vectorized_backtest.v7.feature_cache import prepare,FeatureCache
    root,history=fixture_files(tmp_path);data=SessionData(root,history);cache_root=tmp_path/'cache'
    receipt=prepare(data,cache_root)
    cache=FeatureCache(cache_root,data)
    for begin,end in ((0,80),(1,40),(17,73)):
        expected=data.feature_block(begin,end,[0]);actual=cache.block(begin,end,[0])
        for left,right in zip(expected,actual):np.testing.assert_array_equal(left.numpy(),right)
    assert cache.loads==1 and cache.hits==2
    cached=SessionData(root,history,feature_cache=cache_root)
    cached.prepare_feature_block=lambda *args:pytest.fail('Repeated feature preparation')
    gates=PopulationPrograms([member()]).evaluate(cached,chunk=17)
    assert ((gates&255)==1).all() and ((gates>>8)==1).all()
    assert prepare(data,cache_root)==receipt
    name=next(iter(receipt['files']));(cache_root/name).write_bytes(b'corrupt')
    with pytest.raises(ValueError,match='changed'):cache.block(0,1,[0])
    with pytest.raises(ValueError,match='integrity'):FeatureCache(cache_root,data).block(0,1,[0])


@pytest.mark.parametrize('cost',[0.,10.])
def test_numpy_reference_all_lifecycle_paths(cost):
    from research.vectorized_backtest.v7.reference import replay_reference
    rng=np.random.default_rng(223);prices=np.maximum(2.,10.+np.cumsum(rng.normal(0,.6,(200,4)),axis=0))
    data=session([1.,1.]);data.clocks=len(prices);data.listing_ids=list(range(4))
    data.tensors=dict(mark=torch.from_numpy(prices),observed=torch.from_numpy(rng.random(prices.shape)>.1),
        membership=torch.from_numpy(rng.random(prices.shape)>.4),history_ids=torch.arange(len(prices))[:,None].expand(-1,4).to(torch.int32))
    data.host_tensors=data.tensors
    data.swing_bank=lambda members:(torch.full((len(prices),1),8.,dtype=torch.float64),torch.zeros(len(members),dtype=torch.int64))
    members=[member(cooldown=1,add_minimum_profit=0.,reduce_minimum_profit=0.),member(cooldown=3,reduce_fraction=1.),
        member(swing_left=1,swing_right=1,cooldown=1)]
    gates=torch.from_numpy(rng.integers(0,32,(3,len(prices),4),dtype=np.uint8));execution=Execution(cost_bps=cost)
    actual=replay_cohort([data],members,[gates],execution=execution)[0]
    expected=replay_reference(data,members,gates.numpy(),execution)
    for name,value in actual.items():np.testing.assert_allclose(value.numpy(),expected[name],rtol=1e-10,atol=1e-8)


def test_full30_search_and_exact_completed_resume(tmp_path,monkeypatch):
    from research.vectorized_backtest.v7 import run_search
    inputs=tmp_path/'search-inputs';history=tmp_path/'search-history';output=tmp_path/'search'
    days=[f'day-{i:02d}' for i in range(30)]
    for day in days:
        for root in (inputs,history):
            (root/day).mkdir(parents=True);(root/day/'complete.json').write_text('{}')
    monkeypatch.setattr(run_search,'training_days',lambda root:days)
    class Small:
        def __init__(self,*args,**kwargs):
            self.__dict__.update(session([10.,10.,11.]).__dict__)
            self.identity=dict(synthetic=True)
        def feature_block(self,begin,end,listings):
            values=torch.zeros((len(listings),end-begin,len(CATALOG)))
            return values,torch.ones_like(values,dtype=torch.bool)
        def close(self):self.tensors.clear()
    monkeypatch.setattr(run_search,'SessionData',Small)
    assert run_search.run(inputs,history,output,population_size=10,generations=2,batch_size=5,session_workers=8,device='cpu',backend='eager')==0
    saved=(output/'checkpoint.json').read_bytes();record=json.loads(saved)
    assert record['completed_generations']==2
    assert len(json.loads((output/'generation-0002'/'complete.json').read_text())['session_receipts'])==30
    def unexpected(*args,**kwargs):raise AssertionError('Completed resume reevaluated training')
    monkeypatch.setattr(run_search,'SessionData',unexpected)
    assert run_search.run(inputs,history,output,population_size=10,generations=2,batch_size=5,session_workers=8,device='cpu',backend='eager')==0
    assert (output/'checkpoint.json').read_bytes()==saved


def test_open_branch_and_age_use_pre_execution_state_and_net_fees():
    base=member(target_fraction=.9)
    always=Program((Node(Op.CONSTANT,value=1,unit='bool'),),0)
    open_rules=dict(base.rules);open_rules['exit']=always
    strategy=Individual(base.rules,base.policy,open_rules,{'exit':2}).validate()
    restored=Individual.restore(strategy.payload())
    assert restored.payload()==strategy.payload()
    data=session([10.,10.,11.,12.,13.,14.])
    # Flat branch requests entry; open branch requests exit. Age is measured
    # at the decision close, not the following execution close.
    signals=torch.full((1,6,1),1|(2<<8),dtype=torch.int16)
    result=replay_cohort([data],[strategy],[signals],execution=Execution(cost_bps=10))[0]
    # Entry clock1, age2 decision clock3 -> exit clock4 at13.
    assert result['net_pnl'].item()==pytest.approx(300-1-1.3)
    assert result['filled_batches'].item()==1
    assert result['terminal_valid'].item()


def test_state_signal_selection_scripted_and_accounting_fields_rejected():
    from research.vectorized_backtest.v7.evaluator import state_signals
    signals=torch.tensor([[[1|(2<<8),1|(4<<8)]]],dtype=torch.int32)
    held=torch.tensor([[[False,True]]]);age=torch.tensor([[[0.,3.]]])
    limits=torch.zeros((1,1,1,5));limits[...,2]=4
    expected=state_signals(signals,held,age,limits,True)
    assert expected.tolist()==[[[1,0]]]
    torch.testing.assert_close(torch.jit.script(state_signals)(signals,held,age,limits,True),expected)
    with pytest.raises(ValueError):
        Individual(member().rules,Policy(),minimum_age={'unrealized_pnl':1}).validate()


def test_stateful_program_pipeline_matches_independent_ledger(tmp_path):
    from research.vectorized_backtest.v7.reference import replay_reference
    root,history=fixture_files(tmp_path);data=SessionData(root,history)
    base=member(target_fraction=.9)
    always=Program((Node(Op.CONSTANT,value=1,unit='bool'),),0)
    opened=dict(base.rules);opened['exit']=always
    strategy=Individual(base.rules,base.policy,opened,{'exit':2}).validate()
    gates=PopulationPrograms([strategy]).evaluate(data,chunk=17,listing_batch=1)
    cost=Execution(cost_bps=10)
    expected=replay_reference(data,[strategy],gates.numpy(),cost)
    actual=replay_cohort([data],[strategy],[gates],execution=cost)[0]
    for name in expected:np.testing.assert_allclose(actual[name].numpy(),expected[name],rtol=1e-9,atol=1e-7)
    data.close()


def test_stateful_compiled_graph_cohorts_match_eager(monkeypatch):
    original=torch.compile
    # Exercise fullgraph Dynamo capture on CPU; workstation Inductor/CUDA
    # acceptance remains a separate real-data qualification.
    monkeypatch.setattr(torch,'compile',lambda fn,**kw:original(fn,backend='eager',**kw))
    base=member(cooldown=1,add_minimum_profit=0.,target_fraction=.9)
    strategy=Individual(base.rules,base.policy,base.rules,{'exit':2}).validate()
    data=[session([10.,10.,11.,12.,13.,14.]),session([5.,5.,6.,7.,8.])]
    gates=[torch.full((1,d.clocks,1),1|(6<<8),dtype=torch.int16) for d in data]
    expected=replay_cohort(data,[strategy],gates,execution=Execution(cost_bps=10))
    actual=replay_cohort(data,[strategy],gates,execution=Execution(cost_bps=10),backend='compile')
    for left,right in zip(actual,expected):
        for name in right:torch.testing.assert_close(left[name],right[name],rtol=0,atol=0)
    # Adds clock2/3 must not reset the entry clock1 age: exit clock4.
    assert actual[0]['add_count'].item()==2
    assert actual[0]['closed_positions'].item()==1
