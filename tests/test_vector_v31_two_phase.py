import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
import pytest
from research.vectorized_backtest.v31.calibration import PROBE_VERSION,load_probe
from research.vectorized_backtest.v31.genome import INPUTS,MARKET_INPUTS
from research.vectorized_backtest.v31.features import CATALOG
from research.vectorized_backtest.v31.local_runner import run_session
from research.vectorized_backtest.v31.run_search import digest,source_hash
from research.vectorized_backtest.v31.evaluator import Execution
from research.vectorized_backtest.v6.torch_backtest.runtime import write_json

def metrics(n,sign=1):
    values=np.arange(1,n+1,dtype=float)*sign
    result={k:[0.]*n for k in ('drawdown','maximum_position_risk','maximum_stop_distance','stop_risk_dollar_seconds','capital_dollar_seconds','turnover','worst_position_pnl')}
    result.update(net_pnl=values.tolist(),closed_positions=[1.]*n,winning_positions=[float(sign>0)]*n,terminal_valid=[True]*n,
        ticker_pnl=[{'fixture':float(v)} for v in values],episode_profits=[[float(v)] for v in values],episode_listings=[[0] for _ in values],
        episode_entry_clocks=[[1] for _ in values],episode_exit_clocks=[[2] for _ in values])
    return result

@pytest.fixture
def setup(tmp_path):
    days=['2026-08-03','2026-08-04'];data=dict(days=days,inputs='fixture',history='fixture',indicator_root='fixture',enriched_root='fixture',
        receipts={d:dict(inputs='i',history='h') for d in days},indicator_receipts={d:'a' for d in days},enriched_receipts={d:'e' for d in days})
    training=tmp_path/'training.json';training.write_text(json.dumps(data));folder=tmp_path/'probes';folder.mkdir()
    rng=np.random.default_rng(1);x=rng.uniform(-.8,.8,(2,8,4,INPUTS)).astype(np.float32);x[...,MARKET_INPUTS:]=0.;x[...,2*len(CATALOG):MARKET_INPUTS]=1.
    arrays=dict(inputs=x,known=np.ones(x.shape[:-1]+(len(CATALOG),),bool),eligible=np.ones(x.shape[:-1],bool),held_states=np.zeros(x.shape[:-1]+(INPUTS-MARKET_INPUTS,),np.float32))
    for day in days:
        meta=dict(version=PROBE_VERSION,training_sha256=hashlib.sha256(training.read_bytes()).hexdigest(),days=[day],receipts={day:data['receipts'][day]},validation_opened=False,
            slice_sha256={k:hashlib.sha256(v.tobytes()).hexdigest() for k,v in arrays.items()})
        np.savez(folder/(day+'.npz'),**arrays,metadata=np.asarray(json.dumps(meta)))
    cfg=dict(authorization='v31-training',source_sha256=source_hash(),training_manifest=str(training),execution=Execution().__dict__,device='cpu',backend='eager',batch_size=10,
        inputs='fixture',history='fixture',indicator_root='fixture',enriched_root='fixture',
        input_receipts=data['receipts'],indicator_receipts=data['indicator_receipts'],enriched_receipts=data['enriched_receipts'])
    return SimpleNamespace(days=days,data=data,training=training,folder=folder,config=cfg,root=tmp_path)

class Worker:
    calls=[];stop_after_first=False;loads=uploads=1
    def __init__(self,config,*args):self.config=config
    def load(self,day):return SimpleNamespace(identity=dict(input_sha256='i',history_sha256='h',indicator_sha256='a',enriched_sha256='e'),listing_ids=[0])
    def run(self,day,next_day,payload,generation,token,width):
        self.calls.append((generation,len(payload)))
        write_json(Path(self.config['output'])/f'generation-{generation:04d}'/day/'receipt.json',dict(population_sha256=token,validation_opened=False,execution=self.config['execution'],full_session=True,metrics=metrics(len(payload))))
        if self.stop_after_first and generation==1:(Path(self.config['output'])/'STOP').touch()
        return dict(wall_seconds=0.)
    def close(self):pass

def local(s,day,root,worker=Worker):
    cfg=dict(s.config,initialization_probe=str(s.folder/(day+'.npz')))
    return run_session(cfg,day,root,2,10,worker_factory=worker,initial_multiplier=6)

def test_sixfold_initial_generation_then_normal_population_and_exact_resume(setup):
    s=setup;day=s.days[0];Worker.calls=[];Worker.stop_after_first=False
    assert local(s,day,s.root/'straight')['status']=='complete'
    assert Worker.calls==[(1,60),(2,10)]
    class Interrupted(Worker):calls=[];stop_after_first=True
    assert local(s,day,s.root/'resumed',Interrupted)['status']=='interrupted'
    checkpoint=json.loads((s.root/'resumed/checkpoint.json').read_bytes())
    assert checkpoint['state']['completed']==1 and len(checkpoint['state']['population'])==10
    assert 'species' in checkpoint['state']
    (s.root/'resumed/STOP').unlink()
    Interrupted.stop_after_first=False
    assert local(s,day,s.root/'resumed',Interrupted)['status']=='complete'
    assert Interrupted.calls==[(1,60),(2,10)]
    straight=json.loads((s.root/'straight/checkpoint.json').read_bytes())['state'];resumed=json.loads((s.root/'resumed/checkpoint.json').read_bytes())['state']
    for key in ('population','ids','pedigree','rng','species','archive'):assert straight[key]==resumed[key]
    before=(s.root/'resumed/checkpoint.json').read_bytes();count=len(Interrupted.calls)
    assert local(s,day,s.root/'resumed',Interrupted)['status']=='complete'
    assert (s.root/'resumed/checkpoint.json').read_bytes()==before and len(Interrupted.calls)==count

def test_local_probe_cannot_borrow_another_session(setup):
    s=setup;cfg=dict(s.config,initialization_probe=str(s.folder/(s.days[1]+'.npz')))
    with pytest.raises(ValueError,match='only its own session'):run_session(cfg,s.days[0],s.root/'bad',2,10,worker_factory=Worker)


def test_global_probe_rejects_mislabeled_local_session(setup):
    from research.vectorized_backtest.v31.probes import global_probe
    s=setup;(s.folder/(s.days[0]+'.npz')).write_bytes((s.folder/(s.days[1]+'.npz')).read_bytes())
    with pytest.raises(ValueError,match='session label'):global_probe(s.training,s.days,s.folder)

def test_global_pool_mixes_verified_local_origins_and_calibrated_fresh(setup):
    from research.vectorized_backtest.v31.phase2 import seed_pool,verify_pool,attach_source_ancestry
    from research.vectorized_backtest.v31.probes import global_probe
    from research.vectorized_backtest.v31.genome import Individual
    from research.vectorized_backtest.v31.lineage import Pedigree
    s=setup;Worker.stop_after_first=False
    for day in s.days:local(s,day,s.root/'local'/day)
    from research.vectorized_backtest.v31.local_details import canvas
    assert len(canvas.load_generation(s.root/'local'/s.days[0],1))==60
    path=global_probe(s.training,s.days,s.folder);probe=load_probe(path,s.training)
    assert probe['metadata']['days']==s.days
    pool=seed_pool(s.root/'local',s.days,10,dataset=s.data,probe=probe)
    verify_pool(pool,s.days,dataset=s.data)
    assert pool['expert_count']==7 and pool['fresh_count']==3
    assert {x['day'] for x in pool['sources'] if x}==set(s.days)
    members=[Individual.restore(p) for p in pool['population']];pedigree=Pedigree(3)
    pedigree.birth(members,1,[(-1,-1)]*10,['fresh']*10);attach_source_ancestry(pedigree,pool['sources'])
    assert all(all(a<0 for a,d in pedigree.records[i]['ancestors']) for i in range(7))
    assert all('local_origin' in pedigree.records[i] for i in range(7))
    changed=copy.deepcopy(pool);changed['source_sha256']='foreign';changed['pool_sha256']=digest({k:v for k,v in changed.items() if k!='pool_sha256'})
    with pytest.raises(ValueError,match='source changed'):verify_pool(changed,s.days)
    changed=copy.deepcopy(s.data);changed['history']='other'
    with pytest.raises(ValueError,match='dataset differs'):verify_pool(pool,s.days,dataset=changed)

def test_phase1_progress_counts_only_sealed_sessions(setup):
    from research.vectorized_backtest.v31.monitor import publish_snapshot
    s=setup;root=s.root/'progress'
    write_json(root/'local'/s.days[0]/'status.json',dict(status='running',stage='Initialize 60 candidates'))
    snap=publish_snapshot(root,s.days,30)
    assert (snap['completed'],snap['active'],snap['queued'],snap['failed'])==(0,1,1,0)
    write_json(root/'local'/s.days[0]/'complete.json',dict(status='complete'))
    write_json(root/'local'/s.days[1]/'status.json',dict(status='failed'))
    snap=publish_snapshot(root,s.days,30)
    assert (snap['completed'],snap['active'],snap['queued'],snap['failed'])==(1,0,0,1)


def test_real_phase2_loop_accepts_local_pool_and_keeps_validation_out_of_rng(setup):
    from research.vectorized_backtest.v31 import run_search as search
    from research.vectorized_backtest.v31.phase2 import run_global
    from research.vectorized_backtest.v31.probes import global_probe
    s=setup;Worker.stop_after_first=False
    for day in s.days:local(s,day,s.root/'local'/day)
    probe=global_probe(s.training,s.days,s.folder)
    validation=dict(s.data,days=['2026-09-02']);sign=[1]
    class Panel:
        generation_samples=[]
        def __init__(self,*a):pass
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def __getattr__(self,name):return lambda *a,**kw:None
    class Lanes:
        def __init__(self,c,*a):self.c=c
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def release_plans(self):pass
        def generation(self,days,population,generation,token):
            for day in days:
                write_json(Path(self.c['output'])/f'generation-{generation:04d}'/day/'receipt.json',dict(population_sha256=token,full_session=True,metrics=metrics(len(population),sign[0] if self.c['split']=='validation' else 1)))
            return []
    def dataset(path,role):return s.data if role=='training' else validation
    with patch.object(search,'bind_dataset',dataset),patch.object(search,'SessionLanes',Lanes),patch.object(search,'CampaignProgress',Panel):
        for i in range(2):
            sign[0]=1 if i==0 else -1
            assert run_global(s.training,'validation',s.root/'local',s.root/f'global-{i}',10,2,2020,initialization_probe=probe)==0
    a=json.loads((s.root/'global-0/checkpoint.json').read_bytes());b=json.loads((s.root/'global-1/checkpoint.json').read_bytes())
    for key in ('population','ids','pedigree','rng','species'):assert a['state'][key]==b['state'][key]
    assert sum('local_origin' in r for r in a['state']['pedigree'])==7
    assert a['contract']['initialization']=='v31-local-expert-pool-v1'

def test_local_observer_transitions_to_existing_phase2_panel(monkeypatch,tmp_path):
    from research.vectorized_backtest.v31 import local_details,observe
    class Observer:
        def __init__(self,*a):pass
        def start(self):return self
        def read(self):return dict(global_output=str(tmp_path/'global'))
        def close(self):pass
    seen=[];monkeypatch.setattr(local_details,'DetailObserver',Observer)
    for name in ('rule_lines','detail_lines','session_layout'):monkeypatch.setattr(local_details.canvas,name,getattr(local_details.canvas,name))
    monkeypatch.setattr(observe,'main',lambda argv:seen.append(argv) or 0)
    assert local_details.main(['--active',str(tmp_path/'active.json'),'--once'])==0
    assert seen==[['--output',str(tmp_path/'global'),'--once']]


def test_controller_routes_all_locals_before_global_and_defaults_to_sixfold(setup,monkeypatch):
    from research.vectorized_backtest.v31 import run_train as controller,probes,phase2
    s=setup;days=[f'2026-08-{i:02d}' for i in range(1,25)];valid=[f'2026-09-{i:02d}' for i in range(2,14)]
    dataset=dict(s.data,days=days);calls=[];root=s.root/'controller'
    monkeypatch.setattr(controller,'ROOT',root)
    monkeypatch.setattr(controller,'bind_dataset',lambda path,role:dataset if role=='training' else dict(dataset,days=valid))
    monkeypatch.setattr(probes,'local_probe',lambda train,day,folder:Path(folder)/(day+'.npz'))
    monkeypatch.setattr(probes,'global_probe',lambda train,ds,folder:Path(folder)/'global.npz')
    def session(config,day,output,generations,population,seed,*a,**kwargs):
        calls.append(('local',day,generations,population,seed,kwargs['initial_multiplier']))
        assert config['initialization_probe'].endswith(day+'.npz')
        write_json(Path(output)/'complete.json',dict(status='complete'))
        return dict(status='complete')
    def global_run(training,validation,phase1,output,population,generations,seed,**kwargs):
        assert len(calls)==24
        calls.append(('global',population,generations,seed))
        assert kwargs['session_workers']==1 and kwargs['initialization_probe'].name=='global.npz'
        return 0
    monkeypatch.setattr(controller,'run_session',session);monkeypatch.setattr(phase2,'run_global',global_run)
    assert controller.main(['--training',str(s.training),'--validation','fixture-validation','--output',str(root/'campaign'),
        '--authorize-run','--commit','fixture','--population','10','--generations','2','--global-generations','3',
        '--host-reserve-gib','0'])==0
    assert calls[:24]==[('local',d,2,10,2020,6) for d in days]
    assert calls[-1]==('global',10,3,2020)
    status=json.loads((root/'campaign/status.json').read_bytes());assert status['status']=='complete' and status['completed']==24
    manifest=json.loads((root/'campaign/manifest.json').read_bytes());assert manifest['initial_count']==60


@pytest.mark.parametrize('width,height',[(140,44),(90,28),(70,22)])
def test_regular_phase_panels_render_at_normal_and_compact_sizes(tmp_path,width,height):
    import io
    from rich.console import Console
    from research.vectorized_backtest.v31.local_details import session_layout
    from research.vectorized_backtest.v31.terminal import render
    data=dict(day='2026-08-04',days=['2026-08-04'],target=30,generation=0,running=True,completed_sessions=0,
        workers={'2026-08-04':dict(status='running',stage='Evaluate initial 6144 candidates',generation=1,completed_local_batches=1,total_local_batches=6,completed_seconds=100,total_seconds=19799)})
    for name,panel in [('local',session_layout(data,width,height)),('global',render(dict(status='running',stage='Training',config=dict(population=1024,generations=50,training_sessions=24),top_strategies=[]),width=width,height=height))]:
        stream=io.StringIO();console=Console(file=stream,width=width,height=height,force_terminal=False,color_system=None);console.print(panel)
        text=stream.getvalue();assert '\x1b[' not in text and 'V31' in text
        assert max(map(len,text.splitlines()))<=width
        (tmp_path/f'{name}-{width}x{height}.txt').write_text(text,encoding='utf-8')


def test_local_loop_runs_real_cpu_worker_in_six_bounded_batches(setup,monkeypatch):
    from test_vector_v31 import toy,swings
    from research.vectorized_backtest.v31.session_lanes import Worker as RealWorker
    s=setup;day=s.days[0];data=toy(t=5,u=3)
    data.identity=dict(input_sha256='i',history_sha256='h',indicator_sha256='a',enriched_sha256='e')
    data.receipt=dict(validation_opened=False,listings=[dict(listing_id=f'fixture-{i}') for i in range(3)])
    data.bytes=0;data.file_stats={};data.close=lambda:None
    monkeypatch.setattr(RealWorker,'read_host',lambda self,day:data)
    cfg=dict(s.config,initialization_probe=str(s.folder/(day+'.npz')),feature_cache=None,
        maximum_data_gib=4.,maximum_gate_gib=8.,capture_gib=1.,episode_capacity=128,host_reserve_gib=0.)
    with patch('research.vectorized_backtest.v31.swing_projection.entry_swings',swings):
        assert run_session(cfg,day,s.root/'real-worker'/day,1,10,initial_multiplier=6)['status']=='complete'
    timings=json.loads((s.root/'real-worker'/day/'generation-0001'/day/'timings.json').read_bytes())
    assert [t['candidates'] for t in timings]==[10]*6
    receipt=json.loads((s.root/'real-worker'/day/'generation-0001'/day/'receipt.json').read_bytes())
    assert receipt['evaluated_candidates']==60 and len(receipt['metrics']['net_pnl'])==60
