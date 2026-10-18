import copy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
import pytest
import torch
from research.vectorized_backtest.v28.genome import Individual,Network,Neuron,INPUTS,STAGES,sample,prune
from research.vectorized_backtest.v28.features import CATALOG
from research.vectorized_backtest.v28.composition import cross,compose,mutation
from research.vectorized_backtest.v28.network import NetworkPlan
from research.vectorized_backtest.v28.evaluator import replay_cohort,Execution

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
    members=sample(np.random.default_rng(2020),64,stratified=True)
    for s in STAGES:
        counts=np.bincount([len(m.networks[s].nodes) for m in members],minlength=17)
        assert np.array_equal(counts[1:],np.full(16,4))
    assert max(len(m.networks['entry'].nodes) for m in members)==16

def metrics_fixture(n,sign=1):
    x=np.arange(1,n+1,dtype=float)*sign
    out={k:[0.]*n for k in ('drawdown','maximum_position_risk','maximum_stop_distance','stop_risk_dollar_seconds','capital_dollar_seconds','turnover','worst_position_pnl')}
    out.update(net_pnl=x.tolist(),closed_positions=[1.]*n,winning_positions=[float(sign>0)]*n,terminal_valid=[True]*n,
        ticker_pnl=[{'fixture':float(v)} for v in x],episode_profits=[[float(v)] for v in x],episode_listings=[[0] for _ in x],episode_entry_clocks=[[1] for _ in x],episode_exit_clocks=[[2] for _ in x])
    return out

def test_local_sealed_resume_and_global_seed_pool(tmp_path):
    from research.vectorized_backtest.v28.local_runner import run_session
    from research.vectorized_backtest.v28.run_search import source_hash
    from research.vectorized_backtest.v28.phase2 import seed_pool,verify_pool
    from research.vectorized_backtest.v6.torch_backtest.runtime import write_json
    import json
    cfg=dict(authorization='v28-training',source_sha256=source_hash(),execution=Execution().__dict__,device='cpu',backend='eager',batch_size=10,
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
    import research.vectorized_backtest.v28.run_search as search
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
    pool=dict(version='v28-local-expert-pool-v1',population=payload,population_sha256=search.digest(payload),pool_sha256='fixture',expert_count=0,sources=[None]*10)
    with patch.object(search,'bind_dataset',dataset),patch.object(search,'SessionLanes',Lanes),patch.object(search,'CampaignProgress',Panel),patch('research.vectorized_backtest.v28.phase2.verify_pool'):
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
    a=Network(tuple(Neuron((0,) if i==0 else (INPUTS+i-1,),(1.,),0.) for i in range(6)),0.).validate()
    with pytest.raises(ValueError):compose(np.random.default_rng(1),a,a,True)

def test_old_symbolic_genome_rejected():
    with pytest.raises(ValueError):Individual.restore(dict(version='v27'))

def test_certified_extractor_identity_is_preserved():
    from research.vectorized_backtest.v28.certificates import extractor_hash
    from research.vectorized_backtest.v27 import prepare_enriched
    assert extractor_hash(prepare_enriched.__file__)=='fd33c8bb35752c1fef8e3392bcd8921134edf219483e3cfd1d60a3e84bc191bb'

def test_terminal_compact_and_redirected_output(tmp_path):
    import io
    from rich.console import Console
    from research.vectorized_backtest.v28.monitor import render
    for width in [60,100]:
        stream=io.StringIO();console=Console(file=stream,width=width,force_terminal=False)
        panel,details=render(tmp_path,['2026-08-04'],30,0);console.print(panel)
        text=stream.getvalue();assert '2026-08-04' in text and '0/30' in text and '\x1b' not in text
        assert max(map(len,text.splitlines()))<=width

def test_entry_cannot_read_position_state():
    m=sample(np.random.default_rng(1),1)[0];n=Network((Neuron((0,len(CATALOG)),(1.,1.),0.),),0.)
    with pytest.raises(ValueError):replace(m,networks=dict(m.networks,entry=n)).validate()

def test_missing_input_masks_output():
    m=sample(np.random.default_rng(1),1)[0];n=Network((Neuron((len(CATALOG)-1,),(1.,),1.),),-.5)
    m=replace(m,networks={s:n for s in STAGES});p=NetworkPlan([m],'cpu');d=toy();z=p.signals(d,torch.tensor(1),torch.zeros((1,1,3,9),dtype=torch.float64),torch.zeros((1,1,3)),d.tensors['mark'][1][None,None])
    assert not z.any()

@pytest.mark.skipif(not torch.cuda.is_available(),reason='CUDA required')
def test_cuda_signals_dynamic_state_and_padding():
    members=sample(np.random.default_rng(3),4);cpu=toy();gpu=toy('cuda')
    # Make state dependence explicit, not dependent on random sampling.
    n=Network((Neuron((0,len(CATALOG)+1),(.3,.8),.1),),.1)
    members=[replace(m,networks=dict(m.networks,exit=n)) for m in members]
    p=torch.zeros((1,4,5,9),dtype=torch.float64);p[...,0]=2;p[...,1]=10;p[...,2]=9;p[...,5]=1
    prev=torch.full((1,1,5),10.5,dtype=torch.float64);opened=torch.zeros((1,4,5),dtype=torch.float64)
    a=NetworkPlan(members,'cpu').signals(cpu,torch.tensor(10),p,opened,prev)
    b=NetworkPlan(members,'cuda').signals(gpu,torch.tensor(10,device='cuda'),p.cuda(),opened.cuda(),prev.cuda())
    assert torch.equal(a,b.cpu()) and not b[...,3:].any()

@pytest.mark.skipif(not torch.cuda.is_available(),reason='CUDA required')
def test_financial_cpu_cuda_graph_parity():
    members=sample(np.random.default_rng(2020),4)
    members=[replace(m,policy=replace(m.policy,swing_left=0,swing_right=0),minimum_age={s:0 for s in STAGES}) for m in members]
    results=[]
    with patch('research.vectorized_backtest.v28.swing_projection.entry_swings',swings):
        for device,backend in [('cpu','eager'),('cuda','eager'),('cuda','compile')]:
            data=toy(device);plan=NetworkPlan(members,device)
            results.append(replay_cohort([data],members,[plan],execution=Execution(fee_per_share=.005,minimum_order_fee=1),backend=backend,episode_capacity=64,trace=True)[0])
    for k,v in results[0].items():
        if isinstance(v,torch.Tensor):
            for other in results[1:]:torch.testing.assert_close(v,other[k].cpu(),rtol=1e-10,atol=1e-9)
        else:
            for other in results[1:]:assert v==other[k]
