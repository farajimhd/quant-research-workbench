import copy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
import pytest
import torch
from research.vectorized_backtest.v29.genome import Individual,Network,Neuron,INPUTS,MARKET_INPUTS,STAGES,sample,prune
from research.vectorized_backtest.v29.features import CATALOG
from research.vectorized_backtest.v29.composition import cross,compose,mutation
from research.vectorized_backtest.v29.network import NetworkPlan
from research.vectorized_backtest.v29.evaluator import replay_cohort,Execution

def toy(device='cpu',t=40,u=3):
    rng=np.random.default_rng(71);f=torch.tensor(rng.normal(0,.2,(t*u,len(CATALOG))),device=device,dtype=torch.float32)
    f[:,-1]=float('nan')
    source=torch.arange(t*u,device=device).reshape(t,u);clocks=torch.arange(t,device=device)
    bank=SimpleNamespace(tensors=dict(source=source,ids=source,clocks=clocks))
    columns_cache={}
    def values(rows,ids,clock,columns):
        key=tuple(columns)
        if key not in columns_cache:columns_cache[key]=torch.tensor(columns,device=device)
        z=f[rows].index_select(-1,columns_cache[key]);return torch.nan_to_num(z),torch.isfinite(z)
    bank._values=values
    prices=torch.tensor(10+np.sin(np.arange(t)/3)[:,None]*np.arange(1,u+1)[None,:]*.2,device=device,dtype=torch.float64)
    return SimpleNamespace(device=torch.device(device),clocks=t,listing_ids=np.arange(u),resident_features=bank,
        tensors=dict(mark=prices,observed=torch.ones((t,u),device=device,dtype=torch.bool),membership=torch.ones((t,u),device=device,dtype=torch.bool)))

def swings(data,members):
    return torch.full((1,len(members)),9.,device=data.device,dtype=torch.float64),torch.arange(len(members),device=data.device),torch.zeros((data.clocks,len(data.listing_ids)),device=data.device,dtype=torch.int64)

def test_genetics_roundtrip_and_rng_resume():
    rng=np.random.default_rng(2020);a,b=sample(rng,2);state=copy.deepcopy(rng.bit_generator.state)
    def offspring(r):
        for _ in range(32):
            try:c,_=cross(r,a,b);return mutation(r,c,5)
            except ValueError:continue
        raise AssertionError('No legal child within bounded retry budget')
    c,edits=offspring(rng)
    r2=np.random.default_rng();r2.bit_generator.state=state;d,e2=offspring(r2)
    assert c==d and edits==e2 and Individual.restore(c.payload())==c
    assert len({tuple(e['gene']) for e in edits})==5

def test_initial_topology_stratification():
    from research.vectorized_backtest.v29.config import INITIAL_SIZES,MAX_NODES
    members=sample(np.random.default_rng(2020),2*len(INITIAL_SIZES),stratified=True)
    for s in STAGES:
        assert sorted(len(m.networks[s].nodes) for m in members)==sorted(INITIAL_SIZES*2)
    assert max(len(m.networks['entry'].nodes) for m in members)==max(INITIAL_SIZES)

def metrics_fixture(n,sign=1):
    x=np.arange(1,n+1,dtype=float)*sign
    out={k:[0.]*n for k in ('drawdown','maximum_position_risk','maximum_stop_distance','stop_risk_dollar_seconds','capital_dollar_seconds','turnover','worst_position_pnl')}
    out.update(net_pnl=x.tolist(),closed_positions=[1.]*n,winning_positions=[float(sign>0)]*n,terminal_valid=[True]*n,
        ticker_pnl=[{'fixture':float(v)} for v in x],episode_profits=[[float(v)] for v in x],episode_listings=[[0] for _ in x],episode_entry_clocks=[[1] for _ in x],episode_exit_clocks=[[2] for _ in x])
    return out

def test_local_sealed_resume_and_global_seed_pool(tmp_path):
    from research.vectorized_backtest.v29.local_runner import run_session
    from research.vectorized_backtest.v29.run_search import source_hash
    from research.vectorized_backtest.v29.phase2 import seed_pool,verify_pool
    from research.vectorized_backtest.v6.torch_backtest.runtime import write_json
    import json
    cfg=dict(authorization='v29-training',source_sha256=source_hash(),execution=Execution().__dict__,device='cpu',backend='eager',batch_size=10,
             input_receipts={d:dict(inputs='i',history='h') for d in ['2026-08-03','2026-08-04']},indicator_receipts={d:'a' for d in ['2026-08-03','2026-08-04']},enriched_receipts={d:'e' for d in ['2026-08-03','2026-08-04']})
    class Worker:
        loads=uploads=1
        def __init__(self,c,*args):self.c=c
        def load(self,day):return SimpleNamespace(identity=dict(input_sha256='i',history_sha256='h',indicator_sha256='a',enriched_sha256='e'),listing_ids=[0])
        def run(self,day,next_day,payload,generation,token,width):
            write_json(Path(self.c['output'])/f'generation-{generation:04d}'/day/'receipt.json',dict(population_sha256=token,validation_opened=False,execution=self.c['execution'],full_session=True,metrics=metrics_fixture(len(payload))))
            return dict(wall_seconds=0.)
        def close(self):pass
    from pathlib import Path
    for day in cfg['input_receipts']:
        assert run_session(cfg,day,tmp_path/day,2,10,worker_factory=Worker,initial_multiplier=1)['status']=='complete'
        before=(tmp_path/day/'checkpoint.json').read_bytes()
        assert run_session(cfg,day,tmp_path/day,2,10,worker_factory=Worker,initial_multiplier=1)['status']=='complete'
        assert (tmp_path/day/'checkpoint.json').read_bytes()==before
    pool=seed_pool(tmp_path,list(cfg['input_receipts']),10);verify_pool(pool,list(cfg['input_receipts']))
    assert pool['expert_count']>0 and pool['fresh_count']>=3

def test_global_validation_does_not_change_successor(tmp_path):
    from pathlib import Path
    import json
    import research.vectorized_backtest.v29.run_search as search
    from research.vectorized_backtest.v6.torch_backtest.runtime import write_json
    datasets={role:dict(days=[day],inputs='fixture',history='fixture',indicator_root='fixture',enriched_root='fixture',receipts={},indicator_receipts={},enriched_receipts={}) for role,day in [('training','2026-08-04'),('validation','2026-09-02')]}
    class Panel:
        generation_samples=[]
        def __init__(self,*a):pass
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def __getattr__(self,name):return lambda *a,**kw:None
    sign=[1]
    class Lanes:
        def __init__(self,c,*a):self.c=c
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def release_plans(self):pass
        def generation(self,days,population,generation,token):
            for day in days:write_json(Path(self.c['output'])/f'generation-{generation:04d}'/day/'receipt.json',dict(population_sha256=token,full_session=True,metrics=metrics_fixture(len(population),sign[0] if self.c['split']=='validation' else 1)))
            return []
    def dataset(path,role):return datasets[role]
    payload=[m.payload() for m in sample(np.random.default_rng(99),10)]
    pool=dict(version='v29-local-expert-pool-v1',population=payload,population_sha256=search.digest(payload),pool_sha256='fixture',expert_count=0,sources=[None]*10)
    with patch.object(search,'bind_dataset',dataset),patch.object(search,'SessionLanes',Lanes),patch.object(search,'CampaignProgress',Panel),patch('research.vectorized_backtest.v29.phase2.verify_pool'):
        for i in range(2):
            sign[0]=1 if i==0 else -1
            assert search.run('train','valid',tmp_path/str(i),population_size=10,generations=2,device='cpu',backend='eager',session_workers=1,initial_population=pool)==0
    a=json.loads((tmp_path/'0/checkpoint.json').read_bytes());b=json.loads((tmp_path/'1/checkpoint.json').read_bytes())
    for key in ['population','ids','pedigree','rng']:assert a['state'][key]==b['state'][key]

@pytest.mark.parametrize('sources',[(INPUTS,),(-1,),(999999,)])
def test_cycles_forward_and_unknown_inputs_rejected(sources):
    with pytest.raises(ValueError):Network((Neuron(sources,(1.,),0.),),0.).validate()

@pytest.mark.parametrize('value',[-1.,1.,float('nan')])
def test_threshold_bounds(value):
    with pytest.raises(ValueError):Network((Neuron((0,),(1.,),0.),),value).validate()

def test_composition_preserves_both_parents():
    a=Network((Neuron((0,),(.5,),.1),),.2);b=Network((Neuron((1,),(-.3,),.2),),.3)
    n=compose(np.random.default_rng(1),a,b,True)
    assert n.nodes[:2]==a.nodes+b.nodes and n.nodes[-1].sources==(INPUTS,INPUTS+1)
    assert n.threshold!=a.threshold and n.validate()

def test_size_and_depth_fail_closed():
    from research.vectorized_backtest.v29.config import MAX_DEPTH
    a=Network(tuple(Neuron((0,) if i==0 else (INPUTS+i-1,),(1.,),0.) for i in range(MAX_DEPTH)),0.).validate()
    with pytest.raises(ValueError):compose(np.random.default_rng(1),a,a,True)

def test_old_symbolic_genome_rejected():
    with pytest.raises(ValueError):Individual.restore(dict(version='v27'))

def test_certified_extractor_identity_is_preserved():
    from research.vectorized_backtest.v29.certificates import extractor_hash
    from research.vectorized_backtest.v27 import prepare_enriched
    assert extractor_hash(prepare_enriched.__file__)=='fd33c8bb35752c1fef8e3392bcd8921134edf219483e3cfd1d60a3e84bc191bb'

def test_terminal_compact_and_redirected_output(tmp_path):
    import io
    from rich.console import Console
    from research.vectorized_backtest.v29.monitor import render
    for width in [60,100]:
        stream=io.StringIO();console=Console(file=stream,width=width,force_terminal=False)
        panel,details=render(tmp_path,['2026-08-04'],30,0);console.print(panel)
        text=stream.getvalue();assert '2026-08-04' in text and '0/30' in text and '\x1b' not in text
        assert max(map(len,text.splitlines()))<=width

def test_entry_cannot_read_position_state():
    m=sample(np.random.default_rng(1),1)[0];n=Network((Neuron((0,MARKET_INPUTS),(1.,1.),0.),),0.)
    with pytest.raises(ValueError):replace(m,networks=dict(m.networks,entry=n)).validate()

def test_missing_input_masks_output():
    m=sample(np.random.default_rng(1),1)[0];n=Network((Neuron((len(CATALOG)-1,),(1.,),1.),),-.5)
    m=replace(m,networks={s:n for s in STAGES});p=NetworkPlan([m],'cpu');d=toy();z=p.signals(d,torch.tensor(1),torch.zeros((1,1,3,9),dtype=torch.float64),torch.zeros((1,1,3)),d.tensors['mark'][1][None,None])
    assert not z.any()

@pytest.mark.skipif(not torch.cuda.is_available(),reason='CUDA required')
def test_cuda_signals_dynamic_state_and_padding():
    members=sample(np.random.default_rng(3),4);cpu=toy();gpu=toy('cuda')
    # Make state dependence explicit, not dependent on random sampling.
    n=Network((Neuron((0,MARKET_INPUTS+1),(.3,.8),.1),),.1)
    members=[replace(m,networks=dict(m.networks,exit=n)) for m in members]
    p=torch.zeros((1,4,5,9),dtype=torch.float64);p[...,0]=2;p[...,1]=10;p[...,2]=9;p[...,5]=1
    prev=torch.full((1,1,5),10.5,dtype=torch.float64);opened=torch.zeros((1,4,5),dtype=torch.float64)
    a=NetworkPlan(members,'cpu').signals(cpu,torch.tensor(10),p,opened,prev)
    b=NetworkPlan(members,'cuda').signals(gpu,torch.tensor(10,device='cuda'),p.cuda(),opened.cuda(),prev.cuda())
    assert torch.equal(a,b.cpu()) and not b[...,3:].any()
    reference=NetworkPlan(members,'cuda');reference.register_engine=True
    c=reference.signals(gpu,torch.tensor(10,device='cuda'),p.cuda(),opened.cuda(),prev.cuda())
    assert torch.equal(b,c)


@pytest.mark.skipif(not torch.cuda.is_available(),reason='CUDA required')
def test_maximum_graph_and_missingness_workspace_register_parity():
    from research.vectorized_backtest.v29.genome import random_network
    rng=np.random.default_rng(101);m=sample(rng,1)[0]
    n=random_network(rng,'entry',64)
    m=replace(m,networks={s:n for s in STAGES});d=toy('cuda')
    p=torch.zeros((1,1,3,9),dtype=torch.float64,device='cuda');opened=torch.zeros((1,1,3),device='cuda');price=d.tensors['mark'][1][None,None]
    a=NetworkPlan([m],'cuda');b=NetworkPlan([m],'cuda');b.register_engine=True
    for tick in (1,5,12):
        index=torch.tensor(tick,device='cuda')
        assert torch.equal(a.signals(d,index,p,opened,price),b.signals(d,index,p,opened,price))
    assert a.workspace.numel()==5*64*3


def test_partial_missing_inputs_do_not_veto_observed_inputs():
    m=sample(np.random.default_rng(1),1)[0]
    n=Network((Neuron((0,len(CATALOG)-1,3*len(CATALOG)-1),(.5,.5,.5),1.),),-.5)
    m=replace(m,networks={s:n for s in STAGES})
    d=toy();plan=NetworkPlan([m],'cpu')
    z=plan.signals(d,torch.tensor(1),torch.zeros((1,1,3,9),dtype=torch.float64),torch.zeros((1,1,3)),d.tensors['mark'][1][None,None])
    assert (z==31).all()


def test_scaling_retains_levels_cross_section_and_causality():
    d=toy();members=sample(np.random.default_rng(2),1)
    n=Network((Neuron((0,len(CATALOG)),(1.,1.),0.),),0.)
    members=[replace(members[0],networks={s:n for s in STAGES})]
    plan=NetworkPlan(members,'cpu');a,mask=plan.project(d,torch.tensor(1))
    assert a.shape==(3,3) and torch.equal(a[:,2],mask[:,0].float())
    assert a[:,1].std()>0 and torch.isfinite(a).all()
    # Changing a future row cannot affect current-clock scaling or values.
    original=d.resident_features._values
    def future(rows,ids,clock,columns):
        v,m=original(rows,ids,clock,columns)
        return v+torch.where(clock[:,:,None]>1,10000.,0.),m
    d.resident_features._values=future
    b,_=plan.project(d,torch.tensor(1));assert torch.equal(a,b)


def test_mating_preserves_cooperating_actions_and_local_thresholds():
    from research.vectorized_backtest.v29.composition import exchange
    rng=np.random.default_rng(302);a,b=sample(rng,2)
    for _ in range(30):
        try:c,_=cross(rng,a,b)
        except ValueError:continue
        assert min(sum(c.networks[s]!=p.networks[s] for s in STAGES) for p in (a,b))<=2
    left=Network((Neuron((0,),(1.,),0.),Neuron((INPUTS,),(1.,),0.)),.37)
    right=Network((Neuron((1,),(1.,),0.),),-.21)
    for _ in range(30):
        n=exchange(rng,left,right)
        assert n.threshold in (left.threshold,right.threshold)
    for _ in range(100):
        c,edits=mutation(rng,a,1)
        for edit in edits:
            if edit['field'].startswith('structure'):
                assert c.networks[edit['stage']].threshold==a.networks[edit['stage']].threshold


def test_swing_pair_can_mutate_without_changing_other_policy_genes():
    rng=np.random.default_rng(88);a=sample(rng,1)[0]
    for _ in range(3000):
        c,edits=mutation(rng,a,1)
        if edits[0]['gene']==['policy','swing']:
            c.policy.validate()
            assert (c.policy.swing_left,c.policy.swing_right)!=(a.policy.swing_left,a.policy.swing_right)
            assert replace(c.policy,swing_left=a.policy.swing_left,swing_right=a.policy.swing_right)==a.policy
            break
    else:raise AssertionError('Swing mutation route unreachable')

@pytest.mark.skipif(not torch.cuda.is_available(),reason='CUDA required')
def test_financial_cpu_cuda_graph_parity():
    members=sample(np.random.default_rng(2020),4)
    members=[replace(m,networks={s:replace(n,size_weight=0.,size_bias=0.) for s,n in m.networks.items()},policy=replace(m.policy,swing_left=0,swing_right=0),minimum_age={s:0 for s in STAGES}) for m in members]
    results=[]
    with patch('research.vectorized_backtest.v29.swing_projection.entry_swings',swings):
        for device,backend in [('cpu','eager'),('cuda','eager'),('cuda','compile')]:
            data=toy(device);plan=NetworkPlan(members,device)
            results.append(replay_cohort([data],members,[plan],execution=Execution(fee_per_share=.005,minimum_order_fee=1),backend=backend,episode_capacity=64,trace=True)[0])
    for k,v in results[0].items():
        if isinstance(v,torch.Tensor):
            for other in results[1:]:torch.testing.assert_close(v,other[k].cpu(),rtol=1e-10,atol=1e-9)
        else:
            for other in results[1:]:assert v==other[k]


def test_delayed_memory_is_simultaneous_and_checkpoint_restorable():
    m=sample(np.random.default_rng(10),1)[0]
    n=Network((Neuron((0,-1),(.2,.8),.1,91),),-.9)
    m=replace(m,networks={s:n for s in STAGES});d=toy()
    p=NetworkPlan([m],'cpu');position=torch.zeros((1,1,3,9),dtype=torch.float64);opened=torch.zeros((1,1,3));previous=d.tensors['mark'][1][None,None]
    p.signals(d,torch.tensor(1),position,opened,previous);first=p.memory.clone();state=p.export_state()
    p.signals(d,torch.tensor(1),position,opened,previous);second=p.memory.clone();assert not torch.equal(first,second)
    p.restore_state(state);p.signals(d,torch.tensor(1),position,opened,previous);assert torch.equal(p.memory,second)
    p.reset();p.signals(d,torch.tensor(1),position,opened,previous);assert torch.equal(p.memory,first)


def test_feedback_distinguishes_request_and_actual_fill():
    m=sample(np.random.default_rng(10),1)[0];p=NetworkPlan([m],'cpu');p.prepare(1,2)
    before=torch.zeros((1,1,2,9),dtype=torch.float64);after=before.clone();after[0,0,0,0]=70
    p.ratios[...,0]=.7;signals=torch.ones((1,1,2),dtype=torch.int16);price=torch.full((1,1,2),10.,dtype=torch.float64)
    p.record_outcomes(before,after,price,signals)
    torch.testing.assert_close(p.feedback[...,0],torch.full((1,2),.7))
    torch.testing.assert_close(p.feedback[...,3],torch.tensor([[.7,0.]]))
    assert p.feedback[0,0,7]==0 and p.feedback[0,1,7]==1


def test_homology_matches_origin_not_node_position():
    from research.vectorized_backtest.v29.composition import homologous_weights
    a=Network((Neuron((0,),(.2,),0.,11),Neuron((INPUTS,),(.3,),0.,22)),.2)
    b=replace(a,nodes=(replace(a.nodes[0],weights=(.8,)),replace(a.nodes[1],weights=(.9,))))
    rng=np.random.default_rng(7);seen=False
    for _ in range(30):
        c=homologous_weights(rng,a,b);assert [n.uid for n in c.nodes]==[11,22]
        seen|=c!=a
    assert seen
    unrelated=replace(b,nodes=tuple(replace(n,uid=n.uid+100) for n in b.nodes))
    assert homologous_weights(rng,a,unrelated)==a


def test_internal_growth_preserves_old_edges_and_uid():
    m=sample(np.random.default_rng(112),1)[0];rng=np.random.default_rng(14)
    for _ in range(2000):
        child,edits=mutation(rng,m,1)
        if edits[0]['field']=='structure:grow':
            stage=edits[0]['stage'];old=m.networks[stage];new=child.networks[stage]
            assert len(new.nodes)==len(old.nodes)+1
            assert {n.uid for n in old.nodes}<={n.uid for n in new.nodes}
            assert new.threshold==old.threshold
            break
    else:raise AssertionError('Internal growth route unreachable')


def test_compute_budget_allows_growth_beyond_64_and_packs_actual_nodes():
    from research.vectorized_backtest.v29.genome import random_network
    rng=np.random.default_rng(4);m=sample(rng,1)[0];big=random_network(rng,'entry',100)
    m=replace(m,networks=dict(m.networks,entry=big)).validate();p=NetworkPlan([m],'cpu');p.prepare(1,3)
    assert p.total_nodes==sum(len(n.nodes) for n in m.networks.values())
    assert p.memory.numel()==p.total_nodes*3
    with pytest.raises(ValueError):replace(m,networks={s:big for s in STAGES},minimum_age={}).validate() if 5*len(big.nodes)>512 else replace(m,networks={s:random_network(rng,s,110) for s in STAGES}).validate()


def test_niches_protect_different_structures_without_changing_scores():
    from research.vectorized_backtest.v29.niches import elite_order,groups
    members=sample(np.random.default_rng(81),64);order=np.arange(64);before=order.copy()
    chosen,niches=elite_order(members,order,20)
    assert len(niches)>1 and len(set(chosen[:min(10,len(niches))]))==min(10,len(niches))
    assert np.array_equal(order,before) and set(chosen)==set(order)


@pytest.mark.skipif(not torch.cuda.is_available(),reason='CUDA required')
def test_variable_sizing_financial_kernel_matches_cpu_oracle():
    from research.vectorized_backtest.v29.evaluator import step
    from research.vectorized_backtest.v29.protected_transition import transition
    b=4;u=3;p=torch.zeros((1,b,u,9),dtype=torch.float64);a=torch.zeros((1,b,16),dtype=torch.float64);a[...,8]=float('inf')
    policy=torch.tensor([.03,.05,.25,.03,.01,.02,20,1,0],dtype=torch.float64).reshape(1,1,1,9).expand(1,b,1,9)
    price=torch.full((1,1,u),10.,dtype=torch.float64);observed=torch.ones((1,1,u),dtype=torch.bool);terminal=torch.zeros((1,1,1),dtype=torch.bool)
    signals=torch.ones((1,b,u),dtype=torch.int16);swing=price*.97;clock=torch.tensor(1.,dtype=torch.float64)
    ratios=torch.ones((1,b,u,3),dtype=torch.float64);ratios[:,0,:,0]=.7;ratios[:,1,:,0]=0;ratios[:,2,:,0]=float('nan');ratios[:,3,:,0]=1.1
    args=(p,a,price,price,observed,observed,signals,swing,policy,clock,terminal,1000.,200.,0.,.005,1.)
    expected=step(*args,True,.2,300.,ratios,1.)
    gpuargs=tuple(x.cuda() if isinstance(x,torch.Tensor) else x for x in args)
    actual=transition(*gpuargs,.2,300.,ratios.cuda(),1.)
    for x,y in zip(expected,actual):torch.testing.assert_close(x,y.cpu(),rtol=1e-12,atol=1e-10)
    assert expected[0][0,0,0,0]==70 and not expected[0][0,1:,...,0].any()
    with pytest.raises(ValueError):Execution(entry_dollars=1001).validate()
    with pytest.raises(ValueError):Execution(add_dollars=201).validate()

@pytest.mark.skipif(not torch.cuda.is_available(),reason='CUDA required')
def test_variable_heads_recurrent_gpu_eager_captured_parity():
    from research.vectorized_backtest.v29.profile import compare_financial
    members=sample(np.random.default_rng(31),4)
    members=[replace(m,policy=replace(m.policy,swing_left=0,swing_right=0),minimum_age={s:0 for s in STAGES}) for m in members]
    with patch('research.vectorized_backtest.v29.swing_projection.entry_swings',swings):
        results=[replay_cohort([toy('cuda')],members,[NetworkPlan(members,'cuda')],execution=Execution(fee_per_share=.005,minimum_order_fee=1),backend=backend,trace=True,episode_capacity=64)[0] for backend in ('eager','compile')]
    compare_financial(*results)


def test_entry_feedback_allowed_but_held_state_rejected():
    from research.vectorized_backtest.v29.genome import MARKET_INPUTS
    m=sample(np.random.default_rng(12),1)[0]
    n=Network((Neuron((0,MARKET_INPUTS+7),(.5,.5),0.,5),),0.)
    replace(m,networks=dict(m.networks,entry=n)).validate()
    with pytest.raises(ValueError):replace(m,networks=dict(m.networks,entry=replace(n,nodes=(replace(n.nodes[0],sources=(0,MARKET_INPUTS)),)))).validate()


def test_study_plan_rejects_duplicate_seeds_and_overlapping_holdout(tmp_path):
    from research.vectorized_backtest.v29.experiments import plan_study
    manifest=tmp_path/'training.json';manifest.write_text('{}')
    with patch('research.vectorized_backtest.v29.experiments.bind_dataset',return_value={'days':['2026-08-04']}):
        with pytest.raises(ValueError):plan_study(manifest,[2020,2020])
        with pytest.raises(ValueError):plan_study(manifest,[2020],manifest)
        result=plan_study(manifest,[2020,2021]);assert not result['plan']['launch_authorized']

@pytest.mark.parametrize('device',['cpu','cuda'])
def test_missing_price_does_not_poison_recurrent_state(device):
    if device=='cuda' and not torch.cuda.is_available():pytest.skip('CUDA required')
    m=sample(np.random.default_rng(12),1)[0]
    n=Network((Neuron((0,MARKET_INPUTS+3,-1),(.3,.3,.4),0.,12),),-.8)
    m=replace(m,networks=dict(m.networks,add=n));data=toy(device);plan=NetworkPlan([m],device)
    p=torch.zeros((1,1,3,9),device=device,dtype=torch.float64);opened=p[...,0].clone();previous=torch.full((1,1,3),float('nan'),device=device)
    for i in range(3):plan.signals(data,torch.tensor(i,device=device),p,opened,previous)
    assert torch.isfinite(plan.memory).all() and torch.isfinite(plan.ratios).all()
