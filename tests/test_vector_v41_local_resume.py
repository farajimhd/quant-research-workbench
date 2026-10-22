import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
import pytest
from research.vectorized_backtest.v41.calibration import PROBE_VERSION,load_probe
from research.vectorized_backtest.v41.genome import INPUTS,MARKET_INPUTS
from research.vectorized_backtest.v41.features import CATALOG
from research.vectorized_backtest.v41.local_runner import run_session
from research.vectorized_backtest.v41.run_search import digest,source_hash
from research.vectorized_backtest.v41.evaluator import Execution
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
    cfg=dict(authorization='v41-training',source_sha256=source_hash(),training_manifest=str(training),execution=Execution().__dict__,device='cpu',backend='eager',batch_size=10,
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


@pytest.fixture(autouse=True)
def bounded_initializer(monkeypatch):
    from research.vectorized_backtest.v41.templates import sample_templates
    members,families=sample_templates()
    def initialize(rng,size,probe=None):
        return members[:size],dict(initial_families=families[:size],random_count=0,seed_count=size)
    monkeypatch.setattr('research.vectorized_backtest.v41.local_runner.initialize',initialize)
