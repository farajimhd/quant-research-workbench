import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
from tests.test_vector_v56_campaign import RUNTIME,job,TRAIN
from tests.test_vector_v56_reproduction import members
from research.vectorized_backtest.v42.publication import write_json
from research.vectorized_backtest.v56.session_lanes import output_scope
import torch

class ReplayBatchTests(unittest.TestCase):
    def test_larger_batches_preserve_missing_candidate_order_and_tail(self):
        from research.vectorized_backtest.v56.replay_batches import plan_batches
        pop=members(1)*1200;missing=list(range(100,1200))
        batches=plan_batches(pop,missing,491)
        self.assertEqual(list(map(len,batches)),[1024,76])
        self.assertEqual([i for batch in batches for i in batch],missing)
        self.assertEqual(plan_batches(pop,[],491),[])

    def test_workspace_limit_retains_original_batch_without_dropping_members(self):
        from research.vectorized_backtest.v56.replay_batches import plan_batches
        from research.vectorized_backtest.v56.config import STATES
        pop=members(1)*922;width=491
        per=sum(len(n.nodes) for n in pop[0].networks.values())*width*10+width*len(STATES)*4
        batches=plan_batches(pop,list(range(922)),width,workspace_bytes=per*512)
        self.assertEqual(list(map(len,batches)),[512,410])
        self.assertEqual([i for batch in batches for i in batch],list(range(922)))

class KernelSchedulingTests(unittest.TestCase):
    @unittest.skipUnless(torch.cuda.is_available(),'CUDA required')
    def test_sorted_two_warp_execution_preserves_recurrence_and_tail_listings(self):
        import numpy as np
        from research.vectorized_backtest.v56 import network_cuda as cuda
        from research.vectorized_backtest.v56.network import NetworkPlan
        from research.vectorized_backtest.v56.genome import sample,MARKET_INPUTS
        from research.vectorized_backtest.v42.features import CATALOG
        from tests.test_vector_v56_whole_network import parent,cross
        pop=sample(np.random.default_rng(21),4)
        pop += [cross(parent(0,recurrent=True),parent(1,recurrent=True),mode)[0]
                for mode in ('union','intersection','xor','xnor')]
        plan=NetworkPlan(pop,'cuda');b=len(pop);u=129;plan.prepare(b,u)
        counts=plan.counts.cpu().numpy();order=plan.cuda_network_order.cpu().numpy()
        self.assertEqual(sorted(order.tolist()),list(range(len(counts))))
        self.assertTrue(np.all(np.diff(counts[order])<=0))
        generator=torch.Generator(device='cuda').manual_seed(2021)
        values=torch.rand((u,MARKET_INPUTS),generator=generator,device='cuda')*2-1
        valid=torch.ones((u,len(CATALOG)),device='cuda',dtype=torch.bool);valid[::3]=False
        position=torch.zeros((1,b,u,9),device='cuda',dtype=torch.float64)
        opened=torch.zeros((1,b,u),device='cuda',dtype=torch.float64)
        previous=torch.ones((1,1,u),device='cuda',dtype=torch.float64)
        original=cuda.workspace_kernel
        class Unsorted:
            def __getitem__(self,grid):
                def launch(*args,**kw):
                    kw['num_warps']=4
                    return original[(b,5,args[-2])](*args[:-1],False,**kw)
                return launch
        results=[]
        try:
            for kernel in (Unsorted(),original):
                plan.reset();rows=[]
                with patch.object(cuda,'workspace_kernel',kernel):
                    for tick in range(5):
                        out=cuda.signals(plan,values,valid,position,opened,previous,torch.tensor(tick,device='cuda'))
                        rows.append((out.cpu(),plan.ratios.cpu(),plan.export_state()))
                results.append(rows)
            for before,after in zip(*results):
                self.assertTrue(torch.equal(before[0],after[0]))
                self.assertTrue(torch.equal(before[1],after[1]))
                for key in before[2]:self.assertTrue(torch.equal(before[2][key],after[2][key]),key)
        finally:plan.close()

class PreparationTests(unittest.TestCase):
    def test_capacity_is_bounded_nested_and_restored_after_error(self):
        from research.vectorized_backtest.v56 import validation_cache as c
        with c.validation_scope(capacity=5120):
            self.assertEqual(c._capacity.get(),5120)
            with self.assertRaises(RuntimeError):
                with c.validation_scope(capacity=10):raise RuntimeError('failed')
            self.assertEqual(c._capacity.get(),5120)
        self.assertEqual(c._capacity.get(),c.CAPACITY)
        for invalid in (0,32769,True):
            with self.assertRaises(ValueError):
                with c.validation_scope(capacity=invalid):pass

    def test_cohort_capacity_avoids_eviction_without_caching_invalid_networks(self):
        from research.vectorized_backtest.v56 import validation_cache as c
        from dataclasses import replace
        net=members(1)[0].networks['entry']
        nets=[replace(net) for _ in range(5)]
        with c.validation_scope(capacity=5):
            for n in nets:n.validate()
            self.assertTrue(all(c.known_immutable(n) for n in nets))
            with patch.object(type(net),'_validate_uncached',side_effect=AssertionError('validated twice')):
                for n in nets:n.validate()
            bad=replace(net,threshold=float('nan'))
            with self.assertRaises(ValueError):bad.validate()
            self.assertFalse(c.known_immutable(bad))

class ResidencyTests(unittest.TestCase):
    def test_diagnostic_scope_restores_cache_and_destination_on_failure(self):
        original=('identity',{'elite':'financial-result'});w=SimpleNamespace(config={'output':'main'},resident_results=original,last_result_day='d')
        with self.assertRaisesRegex(RuntimeError,'diagnostic failure'):
            with output_scope(w,'probe',isolate_results=True):
                self.assertIsNone(w.resident_results);self.assertEqual(w.config['output'],'probe');w.resident_results=('mutant',{});w.last_result_day='other';raise RuntimeError('diagnostic failure')
        self.assertIs(w.resident_results,original);self.assertEqual(w.config['output'],'main');self.assertEqual(w.last_result_day,'d')
    def test_normal_scope_preserves_new_training_results(self):
        w=SimpleNamespace(config={'output':'main'},resident_results=None)
        with output_scope(w,'main'):w.resident_results=('new',{'elite':1})
        self.assertEqual(w.resident_results,('new',{'elite':1}))
    def test_main_probe_next_generation_load_market_once_and_keep_one_worker(self):
        from research.vectorized_backtest.v56.campaign_runner import Evolution
        with TemporaryDirectory(dir=RUNTIME) as tmp:
            root=Path(tmp);day=TRAIN[0];hist=root/'history'/day;hist.mkdir(parents=True);write_json(hist/'complete.json',dict(listing_ids=[0]));j=job();j.update(campaign_stage='session',training_days=[day],validation_days=[],source_sha256='fixture');made=[]
            class Worker:
                def __init__(self,cfg,*args):self.config=cfg;self.resident_results=None;self.loads=0;self.loaded=False;self.closes=0;made.append(self)
                def close(self):self.closes+=1
                def run(self,day,next_day,payload,g,token,width):
                    if not self.loaded:self.loads+=1;self.loaded=True
                    diagnostic='mutation-probe' in self.config['output']
                    if diagnostic:assert self.resident_results is None
                    self.resident_results=('probe' if diagnostic else 'main',{'g':g});self.last_result_day=day
                    p=Path(self.config['output'])/f'generation-{g:04d}'/day;p.mkdir(parents=True,exist_ok=True)
                    write_json(p/'receipt.json',dict(day=day,population_sha256=token,full_session=True,execution=j['execution'],split='training',validation_opened=False,case_owners=[None]*2,metrics={'net_pnl':[0,0]}))
            e=Evolution(j,'laptop',root/'run',1,[root]);pop=members(2)
            with patch('research.vectorized_backtest.v56.campaign_runner.config',return_value={'device':'cpu','history':str(root/'history')}),patch('research.vectorized_backtest.v56.campaign_runner.ensure_day'),patch('research.vectorized_backtest.v56.session_lanes.Worker',Worker):
                main=e.worker.config['output'];e.evaluate('training',[day],pop,1);saved=e.direct.resident_results;e.worker.config['output']=str(e.root/'mutation-probe-replay');e.evaluate('training',[day],pop,1);self.assertIs(e.direct.resident_results,saved);e.worker.config['output']=main;e.evaluate('training',[day],pop,2)
            self.assertEqual(len(made),1);self.assertEqual(made[0].loads,1);self.assertEqual(made[0].closes,0);self.assertEqual(made[0].resident_results,('main',{'g':2}));e.direct.close()

    def test_parallel_lane_request_routes_diagnostic_and_restores_training_cache(self):
        import research.vectorized_backtest.v56.session_lanes as module
        from research.vectorized_backtest.v56.identity import digest
        pop=members(2);payload=[m.payload() for m in pop];old=('train',{'elite':1})
        class Worker:
            def __init__(self):self.config={'output':'main'};self.resident_results=old;self.last_result_day='d'
            def run(self,day,next_day,payload,g,token,width):
                self.assertions=(self.config['output'],self.resident_results);self.resident_results=('probe',{})
                return dict(day=day,clocks=2,timings=[])
        worker=Worker()
        class Future:
            def __init__(self,value):self.value=value
            def result(self):return self.value
        class Pool:
            def submit(self,fn,*args):return Future(fn(*args))
        panel=SimpleNamespace(begin_sessions=lambda d:None,session_progress=lambda d:None,complete_sessions=lambda n,t:None)
        lanes=module.SessionLanes({'shared_replay':False},1,panel);lanes.widths={'d':1};lanes.pools=[Pool()]
        try:
            with patch.object(module,'_worker',worker,create=True):lanes.generation(['d'],pop,1,digest(payload),output_override='probe',isolate_results=True)
            self.assertEqual(worker.assertions,('probe',None));self.assertIs(worker.resident_results,old);self.assertEqual(worker.config['output'],'main')
        finally:lanes.events.close();lanes.events.join_thread()

    def test_generated_background_entry_is_safe_to_import_in_spawned_workers(self):
        import json,runpy,sys
        from research.vectorized_backtest.v56.campaign_launch import prepare_entries
        with TemporaryDirectory(dir=RUNTIME) as tmp:
            root=Path(tmp);code=root/'code';code.mkdir();write_json(code/'deployment.json',dict(commit='test',files={}))
            j=dict(campaign=str(root),commit='test',machines={n:dict(code=str(code),hostname='test-host',python=sys.executable) for n in ('laptop','workstation')});p=root/'full-job.json';write_json(p,j)
            with patch('research.vectorized_backtest.v56.campaign_launch.check_job'):prepare_entries(p)
            for node in ('laptop','workstation'):
                values=runpy.run_path(str(root/('entry-'+node+'.py')),run_name='__mp_main__')
                self.assertIn('main',values);self.assertFalse((root/(node+'-run.log')).exists())
