import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import json,copy,unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
from research.vectorized_backtest.v56.identity import digest
from research.vectorized_backtest.v56.campaign_contract import check_structure
from research.vectorized_backtest.v56.ticker_selection import specialist_pools
from tests.test_vector_v56_mutation_probe import metrics,seed_fixture
from research.vectorized_backtest.v42.publication import write_json

RUNTIME=Path('D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v56/qualification/full-tests')
RUNTIME.mkdir(parents=True,exist_ok=True)
TRAIN=[f'2026-08-{i:02d}' for i in range(1,25)]
VALID=[f'2026-09-{i:02d}' for i in range(1,13)]
def job():
    return dict(training_days=TRAIN,validation_days=VALID,machines={'laptop':{'session_days':TRAIN[:8]},'workstation':{'session_days':TRAIN[8:]}},global_node='workstation',global_session_workers=2,phase1_generations=50,phase2_generations=50,population=1024,initial_multiplier=6,seed=2021,batch_size=512,ticker_parent_fraction=.25,selection_risk_caps=False,objective=dict(drawdown_weight=2.,loss_weight=1.),execution=dict(risk_limits_enabled=False),expert_capacity_per_session=128,mutation_probe_size=32,session_probe_sha256={d:'probe' for d in TRAIN})

class CampaignTests(unittest.TestCase):
    def test_exact_two_host_24_12_plan_and_forbidden_variants(self):
        j=job();check_structure(j,TRAIN,VALID)
        changes=[('global_node','laptop'),('global_session_workers',1),('phase1_generations',49),('ticker_parent_fraction',0.),('execution',{'risk_limits_enabled':True})]
        for k,v in changes:
            x=copy.deepcopy(j);x[k]=v
            with self.assertRaises(ValueError):check_structure(x,TRAIN,VALID)
        x=copy.deepcopy(j);x['machines']['laptop']['session_days'].append(TRAIN[8])
        with self.assertRaises(ValueError):check_structure(x,TRAIN,VALID)
        with self.assertRaises(ValueError):check_structure(j,TRAIN,TRAIN[:12])
    def test_ticker_costs_sum_across_sessions_before_parent_ranking(self):
        ordered=[{'ticker_score':[{'a':100.,'b':1.},{'a':10.}]},{'ticker_score':[{'a':-200.},{'a':20.,'b':7.}]}]
        pools=specialist_pools(ordered,np.ones(2,bool),np.ones(2,bool),np.arange(2))
        self.assertEqual(pools,{'a':[1,0],'b':[1,0]})
    def test_parallel_transport_keeps_exact_population_hash(self):
        from research.vectorized_backtest.v56.session_lanes import SessionLanes
        from tests.test_vector_v56_reproduction import members
        pop=members(2);token=digest([m.payload() for m in pop]);seen=[]
        class Future:
            def result(self):return {'timings':[],'day':TRAIN[0],'clocks':2}
        class Pool:
            def submit(self,fn,day,next_day,payload,generation,got,width):
                seen.append((payload,got));return Future()
        panel=SimpleNamespace(begin_sessions=lambda d:None,session_progress=lambda d:None,complete_sessions=lambda n,s:None)
        lanes=SessionLanes({'shared_replay':False},1,panel);lanes.widths={TRAIN[0]:1};lanes.pools=[Pool()]
        lanes.generation(TRAIN[:1],pop,1,token)
        self.assertEqual(digest(seen[0][0]),token);self.assertEqual(seen[0][1],token)
    def test_parallel_evaluation_keeps_manifest_order_and_validation_authority(self):
        from research.vectorized_backtest.v56.campaign_runner import Evolution
        from tests.test_vector_v56_reproduction import members
        pop=members(2)
        with TemporaryDirectory(dir=RUNTIME) as tmp:
            j=job();j.update(campaign_stage='global',source_sha256='fixture');e=Evolution(j,'workstation',tmp,2,[tmp]);observed=[]
            class Lanes:
                def __init__(self,cfg,workers,panel):
                    self.cfg=cfg;observed.append((cfg['split'],workers))
                    assert cfg['compile_cache_root'].replace('\\','/').startswith('D:/TradingML/runtimes/v56c/')
                    assert len(cfg['compile_cache_root'])<80
                def __enter__(self):return self
                def __exit__(self,*args):pass
                def generation(self,days,pop,generation,token):
                    for i,d in reversed(list(enumerate(days))):
                        r=Path(self.cfg['output'])/f'generation-{generation:04d}'/d;r.mkdir(parents=True)
                        write_json(r/'receipt.json',dict(day=d,population_sha256=token,full_session=True,execution=j['execution'],split=self.cfg['split'],validation_opened=self.cfg['split']=='validation',case_owners=[None]*2,metrics={'net_pnl':[i,i]}))
            with patch('research.vectorized_backtest.v56.campaign_runner.config',side_effect=lambda job,node,role:{'split':role}),patch('research.vectorized_backtest.v56.campaign_runner.ensure_day'),patch('research.vectorized_backtest.v56.session_lanes.SessionLanes',Lanes):
                rows,refs=e.evaluate('training',TRAIN,pop,1);self.assertEqual([x['net_pnl'][0] for x in rows],list(range(24)))
                rows,refs=e.evaluate('validation',VALID,pop,1);self.assertEqual(len(rows),12)
                with self.assertRaises(ValueError):e.evaluate('training',VALID,pop,1)
                self.assertEqual(observed,[('training',2),('validation',2)]);e.close_lanes()
    def test_all_24_training_sessions_and_12_validations_cannot_change_successor(self):
        from research.vectorized_backtest.v56.full_controller import Controller
        from research.vectorized_backtest.v56.genome import sample
        outcomes=[]
        for validation_value in (-10000.,10000.):
            with TemporaryDirectory(dir=RUNTIME) as tmp:
                c=Controller.__new__(Controller);c.root=Path(tmp);c.publish=lambda **v:None
                c.job=dict(population=10,initial_multiplier=3,seed=2021,phase2_generations=3,training='fixture',initialization_probe='fixture',source_sha256='source',objective=dict(drawdown_weight=2.,loss_weight=1.),training_sha256='train',validation_sha256='val',selection={},mutation_probe_size=0,training_days=TRAIN)
                calls=[]
                def evaluate(role,days,pop,g):
                    calls.append((role,len(days)));return [metrics(10. if role=='training' else validation_value,len(pop)) for d in days],{}
                c.evaluate=evaluate
                with patch('research.vectorized_backtest.v42.calibration.load_probe',return_value={}),patch('research.vectorized_backtest.v56.full_seed.global_seed',side_effect=seed_fixture),patch('research.vectorized_backtest.v56.initial_population.immigrant',side_effect=lambda rng,probe:sample(rng,1)[0]):c.global_run({},TRAIN,VALID)
                state=json.loads((c.root/'global/checkpoint.json').read_bytes())['state'];outcomes.append(digest({k:state[k] for k in ('population','ids','rng','pedigree')}))
                self.assertEqual(calls,[('training',24),('validation',12)]*3)
                before=digest(state)
                with patch('research.vectorized_backtest.v42.calibration.load_probe',return_value={}),patch('research.vectorized_backtest.v56.full_seed.global_seed',side_effect=AssertionError('Resume must not reinitialize')):c.global_run({},TRAIN,VALID)
                self.assertEqual(calls,[('training',24),('validation',12)]*3)
                self.assertEqual(digest(json.loads((c.root/'global/checkpoint.json').read_bytes())['state']),before)
                seal=json.loads((c.root/'global/generation-0001/validation.json').read_bytes());self.assertEqual(seal['successor_sha256_before'],seal['successor_sha256_after'])
        self.assertEqual(*outcomes)
    def test_stop_roots_and_resident_cache_session_boundary(self):
        from research.vectorized_backtest.v56.session_lanes import Worker
        with TemporaryDirectory(dir=RUNTIME) as tmp:
            w=Worker.__new__(Worker);w.config={'stop_roots':[tmp]};w.resident_results=('old-day',{});w.last_result_day='old'
            (Path(tmp)/'STOP').write_text('test')
            with self.assertRaises(InterruptedError):w.run('new',None,[],1,'token',1)
            self.assertIsNone(w.resident_results)


class ProcessFixtureWorker:
    def __init__(self,cfg,events,lane):self.config=cfg;self.events=events;self.lane=lane;self.plans={}
    def run(self,day,next_day,payload,generation,token,width):
        import time
        from research.vectorized_backtest.v56.identity import digest
        if digest(payload)!=token:raise ValueError('Changed population transport')
        self.config['fixture_barrier'].wait(20)
        started=time.time();time.sleep(.02)
        return {'day':day,'clocks':3,'timings':[{'pid':os.getpid(),'lane':self.lane,'started':started,'finished':time.time()}]}
    def close(self):pass

def initialize_process_fixture(cfg,events,lane):
    import research.vectorized_backtest.v56.session_lanes as module
    module._worker=ProcessFixtureWorker(cfg,events,lane)

class ParallelProcessTests(unittest.TestCase):
    def test_real_spawn_pool_runs_two_distinct_session_owners_and_cleans_up(self):
        import multiprocessing as mp
        import psutil
        from research.vectorized_backtest.v56.session_lanes import SessionLanes
        from tests.test_vector_v56_reproduction import members
        pop=members(2);token=digest([m.payload() for m in pop]);panel=SimpleNamespace(begin_sessions=lambda d:None,session_progress=lambda d:None,complete_sessions=lambda n,s:None)
        cfg={'device':'cpu','capture_gib':1.,'shared_replay':False,'fixture_barrier':mp.get_context('spawn').Barrier(2)}
        with patch('research.vectorized_backtest.v56.session_lanes.initialize',initialize_process_fixture):
            with SessionLanes(cfg,2,panel) as lanes:
                lanes.widths={d:2 for d in TRAIN[:4]};timings=lanes.generation(TRAIN[:4],pop,1,token)
        pids={t['pid'] for t in timings};self.assertEqual(len(pids),2)
        for pid in pids:self.assertFalse(psutil.pid_exists(pid))
        for start in (0,2):self.assertLess(max(x['started'] for x in timings[start:start+2]),min(x['finished'] for x in timings[start:start+2]))

class ExportTests(unittest.TestCase):
    def test_export_reconciles_sealed_financial_scores_and_rejects_tampering(self):
        from research.vectorized_backtest.v56.campaign_export import publish,records
        from research.vectorized_backtest.v56.full_common import aggregate,serializable
        from research.vectorized_backtest.v56.local_runner import CompactPedigree
        from research.vectorized_backtest.v56.lineage import program_key
        from research.vectorized_backtest.v42.publication import write_checkpoint
        from research.vectorized_backtest.v42.distributed_assets import sha
        from tests.test_vector_v56_reproduction import members
        j=job();j['source_sha256']='fixture';day=TRAIN[0]
        with TemporaryDirectory(dir=RUNTIME) as tmp:
            root=Path(tmp);source=root/'source';g=source/'global';f=g/'generation-0050';f.mkdir(parents=True)
            pop=members(2);payload=[m.payload() for m in pop];token=digest(payload);ped=CompactPedigree(3);ids=ped.birth(pop,1,[(-1,-1)]*2,['fresh']*2).tolist();m=metrics(-10.,2)
            receipt=root/'financial.json';write_json(receipt,dict(population_sha256=token,full_session=True,split='training',validation_opened=False,execution=j['execution'],metrics=m))
            write_json(f/'population.json',dict(population=payload,ids=ids,population_sha256=token))
            write_json(f/'training.json',dict(population_sha256=token,ids=ids,panel=serializable(aggregate([m])),receipts={day:dict(receipt=str(receipt),receipt_sha256=sha(receipt))}))
            write_json(f/'evaluated-pedigree.json',list(ped.records.values()));write_json(f/'validation.json',dict(opened=False))
            seal={k:sha(f/name) for name,k in [('population.json','population_sha256'),('training.json','training_sha256'),('evaluated-pedigree.json','evaluated_pedigree_sha256'),('validation.json','validation_sha256')]}
            write_json(f/'complete.json',seal)
            state=dict(completed=50,population=payload,ids=ids,seals={'50':sha(f/'complete.json')})
            contract=dict(source_sha256='fixture',objective=j['objective'],execution=j['execution'],training_days=[day],validation_days=[],campaign_context={'parent_job_sha256':digest(j)})
            write_checkpoint(g/'checkpoint.json',contract,state);write_json(g/'complete.json',dict(completed=50,checkpoint_sha256=sha(g/'checkpoint.json')))
            target=root/'bundle';e=publish(source,target,j,day);self.assertEqual(len(e['records']),2);self.assertTrue(all(x['score']<0 for x in e['records']))
            (target/'receipt.json').write_text('{}')
            with self.assertRaises(ValueError):records(target,j,day)


class PromotionTests(unittest.TestCase):
    def test_global_promotion_deduplicates_and_namespaces_session_ancestry(self):
        from dataclasses import replace
        from research.vectorized_backtest.v56.campaign_seed import seed
        from research.vectorized_backtest.v56.local_runner import CompactPedigree
        from research.vectorized_backtest.v56.lineage import program_key
        from tests.test_vector_v56_whole_network import parent
        base=parent(0)
        def member(threshold):return replace(base,networks=dict(base.networks,entry=replace(base.networks['entry'],threshold=threshold))).validate()
        j=job();j.update(campaign_stage='global',source_sha256='fixture')
        exports={d:dict(source_sha256='fixture',objective=j['objective'],validation_opened=False,records=[],export_sha256=d) for d in TRAIN}
        for day,threshold in ((TRAIN[0],-.5),(TRAIN[1],-.25),(TRAIN[2],-.5)):
            m=member(threshold);ped=CompactPedigree(3);ped.birth([base,member(-.75)],1,[(-1,-1)]*2,['fresh']*2);ids=ped.birth([m],2,[(0,1)],['crossover']);serial=int(ids[0])
            exports[day]['records']=[dict(payload=m.payload(),program_sha256=program_key(m),serial=serial,generation=50,score=-1.,pedigree=ped.records[serial])]
        def fresh(rng,size,probe):return [member((i+1)/10000) for i in range(size)],{}
        with patch('research.vectorized_backtest.v42.initial_population.initialize_random',side_effect=fresh):result=seed(exports,j,{})
        self.assertEqual((result['expert_count'],result['fresh_count']),(2,6142));self.assertEqual(len(result['population']),6144);self.assertEqual(len(set(result['ids'])),6144)
        first,second=result['pedigree'][:2];self.assertFalse(set(a for a,d in first['ancestors'])&set(a for a,d in second['ancestors']))
        self.assertEqual(result['sources'][0]['export_sha256'],TRAIN[0]);self.assertEqual(result['sources'][1]['export_sha256'],TRAIN[1])
        with self.assertRaises(ValueError):seed({TRAIN[0]:exports[TRAIN[0]]},j,{})


class DirectWorkerAdmissionTests(unittest.TestCase):
    def test_single_session_path_sets_same_eighty_percent_gpu_admission_as_pilot(self):
        from research.vectorized_backtest.v56.campaign_runner import Evolution
        from tests.test_vector_v56_reproduction import members
        with TemporaryDirectory(dir=RUNTIME) as tmp:
            root=Path(tmp);day=TRAIN[0];history=root/'history'/day;history.mkdir(parents=True);write_json(history/'complete.json',dict(listing_ids=[0]))
            j=job();j.update(campaign_stage='session',training_days=[day],validation_days=[],source_sha256='fixture');pop=members(2);configs=[]
            class Worker:
                def __init__(self,cfg,*args):self.cfg=cfg;self.config=cfg;self.resident_results=None;configs.append(cfg)
                def run(self,day,next_day,payload,generation,token,width):
                    assert self.cfg['lane_memory_bytes']==8000
                    p=Path(self.cfg['output'])/f'generation-{generation:04d}'/day;p.mkdir(parents=True)
                    write_json(p/'receipt.json',dict(day=day,population_sha256=token,full_session=True,execution=j['execution'],split='training',validation_opened=False,case_owners=[None]*2,metrics={'net_pnl':[0,0]}))
                def close(self):pass
            e=Evolution(j,'laptop',root/'runner',1,[root])
            with patch('research.vectorized_backtest.v56.campaign_runner.config',return_value={'device':'cuda','history':str(root/'history')}),patch('research.vectorized_backtest.v56.campaign_runner.ensure_day'),patch('research.vectorized_backtest.v56.session_lanes.Worker',Worker),patch('torch.cuda.empty_cache'),patch('torch.cuda.mem_get_info',return_value=(10000,12000)):
                rows,refs=e.evaluate('training',[day],pop,1)
            self.assertEqual(configs[0]['lane_memory_bytes'],8000);self.assertEqual(len(rows),1);e.direct.close()
