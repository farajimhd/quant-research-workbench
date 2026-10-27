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
