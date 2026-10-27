import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import copy,json,unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import numpy as np
from research.vectorized_backtest.v56.identity import digest
from research.vectorized_backtest.v56.blind_mix import mix,METHODS,reproduce
from research.vectorized_backtest.v56.genome import sample
from research.vectorized_backtest.v56.local_runner import CompactPedigree
from research.vectorized_backtest.v56.evolution_report import summarize,publish,expose
from research.vectorized_backtest.v56.resume_authority import predecessor,accept_checkpoint
from research.vectorized_backtest.v42.publication import write_json
from research.vectorized_backtest.v42.distributed_assets import sha

RUNTIME=Path('D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v56/qualification/update-tests')
RUNTIME.mkdir(parents=True,exist_ok=True)

def spawned_session_fixture(job,day):
    import time
    from research.vectorized_backtest.v56.campaign_runner import session_unit
    from research.vectorized_backtest.v6.torch_backtest.materialize import owned_run
    root=Path(job['campaign']);probe=root/'probe';probe.write_bytes(b'fixture')
    class Evolution:
        def __init__(self,derived,node,path,workers,stops):self.path=path;self.derived=derived
        def run(self,exports):
            self.path.mkdir(parents=True,exist_ok=True)
            with owned_run(self.path,version='test'):
                (root/(day+'.ready')).write_text(str(os.getpid()))
                deadline=time.monotonic()+30
                while len(list(root.glob('*.ready')))<2:
                    if time.monotonic()>deadline:raise RuntimeError('Second session did not start')
                    time.sleep(.02)
                assert self.derived['independent_session_lanes']==2
    with patch('research.vectorized_backtest.v56.campaign_runner.Evolution',Evolution),patch('research.vectorized_backtest.v56.campaign_runner.ensure_day'),patch('research.vectorized_backtest.v56.campaign_runner.probe',return_value=probe),patch('research.vectorized_backtest.v56.campaign_runner.export_session'):
        result=session_unit(job,'workstation',day)
    return dict(result,pid=os.getpid())

class UpdateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.pop=sample(np.random.default_rng(31),24)

    def test_all_mixing_methods_keep_all_sessions_and_budget_deterministically(self):
        before=digest([m.payload() for m in self.pop])
        donors=[(str(i),i,m) for i,m in enumerate(self.pop)]
        for method in METHODS:
            a,meta=mix(donors,np.random.default_rng(123),method)
            b,other=mix(donors,np.random.default_rng(123),method)
            self.assertEqual(digest(a.payload()),digest(b.payload()))
            self.assertEqual(meta,other);self.assertEqual(set(meta['source_sessions']),{str(i) for i in range(24)})
            self.assertLessEqual(meta['nodes'],1024);a._validate_uncached() if hasattr(a,'_validate_uncached') else a.validate()
            for n in a.networks.values():n._validate_uncached()
            self.assertGreater(meta['depth'],1)
        self.assertEqual(before,digest([m.payload() for m in self.pop]))

    def test_bounded_quota_generation_and_no_random_replacement(self):
        experts={str(i):[(128*i+k,self.pop[i]) for k in range(128)] for i in range(24)}
        children,meta,report=reproduce(experts,2021,quota=1)
        self.assertEqual(len(children),6);self.assertEqual([m['group'] for m in meta],['2','3-4','5-8','9-12','13-23','24'])
        self.assertFalse(report['evaluated'])
        with patch('research.vectorized_backtest.v56.blind_mix.mix',side_effect=ValueError('fixture no contribution')):
            with self.assertRaisesRegex(RuntimeError,'quota unfilled'):reproduce(experts,1,quota=1)

    def test_founder_attribution_survives_but_does_not_transfer_results(self):
        p=CompactPedigree();ids=p.birth(self.pop[:2],1,[(-1,-1)]*2,['fresh']*2)
        for i in ids:p.records[int(i)].update(global_founder=True,founder_index=int(i),founder_mask=hex(1<<int(i)),founder_groups=['24'],founder_methods=['tree:xor'],source_sessions=[str(i)])
        child=p.birth(self.pop[2:3],2,[(0,1)],['crossover'])[0]
        self.assertEqual(p.records[int(child)]['founder_mask'],'0x3');self.assertFalse(p.records[int(child)]['global_founder'])
        panel={k:[1.,2.,3.] for k in ('score','net_pnl','gross_losses','drawdown','closed_positions')};panel['eligible']=[True]*3
        with TemporaryDirectory(dir=RUNTIME) as tmp:
            root=Path(tmp);folder=root/'generation-0002';folder.mkdir()
            report=publish(root,folder,[0,1,int(child)],panel,list(p.records.values()),{int(child)},{},2)
            self.assertFalse((root/'evolution-report.json').exists())
            write_json(folder/'complete.json',{});expose(root,folder)
            founders=json.loads((root/'founder-results.json').read_bytes())
            self.assertEqual(set(founders),{'0','1'});self.assertEqual(founders['0']['score'],1.)
            family=next(g for g in report['groups'] if g['category']=='family')
            self.assertEqual(family['original_founders'],2);self.assertEqual(family['evaluated'],3)

    def test_authorized_resume_rejects_state_or_semantic_changes(self):
        old={k:'same' for k in ('training_sha256','validation_sha256','objective','execution','selection','population','initial_multiplier','seed','phase1_generations','phase2_generations','ticker_parent_fraction','batch_size','asset_sha256')}
        old.update(training_days=['day'],validation_days=['valid'],source_sha256='old')
        with TemporaryDirectory(dir=RUNTIME) as tmp:
            path=Path(tmp)/'old.json';write_json(path,old)
            contract=dict(source_sha256='old',campaign_context=dict(parent_job_sha256=digest(old)),seed=2021)
            saved=dict(contract=contract,state_sha256='state');cp=Path(tmp)/'checkpoint.json';write_json(cp,saved)
            job=dict(old,source_sha256='new',campaign_stage='session',validation_days=[],resume_transition=dict(predecessor_job=str(path),predecessor_job_sha256=sha(path),checkpoints={'day':dict(checkpoint_sha256=sha(cp),state_sha256='state',contract_sha256=digest(contract))}))
            expected=dict(contract,source_sha256='new',campaign_context=dict(parent_job_sha256='new-parent'))
            self.assertEqual(predecessor(job),old);accept_checkpoint(job,cp,saved,expected)
            bad=copy.deepcopy(saved);bad['state_sha256']='changed'
            with self.assertRaises(ValueError):accept_checkpoint(job,cp,bad,expected)
            bad=dict(job,objective='changed')
            with self.assertRaises(ValueError):predecessor(bad)

    def test_ui_navigation_and_compact_redirected_render(self):
        from research.vectorized_backtest.v56.campaign_progress import Model,render
        from rich.console import Console
        import io
        job=dict(campaign=str(RUNTIME),transport=str(RUNTIME),workstation_share='unused',training_days=['day'],machines={'laptop':dict(session_days=['day'])})
        with patch('research.vectorized_backtest.v56.campaign_progress.remote',side_effect=lambda p,s:Path(p)):
            model=Model(job);model.key('\r');self.assertEqual(model.day,'day');self.assertEqual(model.view,'ranking')
            model.key('o');self.assertEqual(model.view,'operators');model.key('h');self.assertEqual(model.view,'overview')
            for width,height in ((80,24),(160,45)):
                stream=io.StringIO();console=Console(file=stream,width=width,height=height,force_terminal=False,color_system=None)
                console.print(render(model,width,height));self.assertNotIn('\x1b',stream.getvalue());self.assertIn('overview',stream.getvalue().lower())

    def test_single_pass_session_panels_equal_separate_aggregation(self):
        from research.vectorized_backtest.v56.full_common import aggregate
        from tests.test_vector_v56_mutation_probe import metrics
        ordered=[metrics(float(i)-12,len(self.pop)) for i in range(24)]
        sink=[];actual=aggregate(ordered,session_sink=sink);expected=aggregate(ordered)
        for key in actual:np.testing.assert_array_equal(actual[key],expected[key])
        for panel,source in zip(sink,ordered):
            separate=aggregate([source])
            for key in panel:np.testing.assert_array_equal(panel[key],separate[key])

    def test_real_spawned_sessions_have_separate_owners_and_overlap(self):
        from research.vectorized_backtest.v56.campaign_runner import session_pool
        with TemporaryDirectory(dir=RUNTIME) as tmp:
            job=dict(campaign=tmp,transport=tmp,phase1_generations=50,machines={'workstation':dict(session_workers=2)})
            with session_pool(tmp) as pool:
                futures=[pool.submit(spawned_session_fixture,job,day) for day in ('one','two')]
                rows=[f.result(timeout=60) for f in futures]
            self.assertEqual(len({r['pid'] for r in rows}),2)
            self.assertEqual({r['day'] for r in rows},{'one','two'})
            self.assertFalse((Path(tmp)/'STOP').exists())

    def test_cache_transition_needs_exact_receipt_and_equal_execution(self):
        from research.vectorized_backtest.v56.resume_authority import accept_cache
        with TemporaryDirectory(dir=RUNTIME) as tmp:
            path=Path(tmp)/'cache.json';record=dict(identity=dict(source='old',execution={'risk':False},data='same'),results={});write_json(path,record)
            config=dict(result_cache_transition={sha(path):dict(source='old')});expected=dict(record['identity'],source='new')
            accept_cache(config,path,record,expected)
            with self.assertRaises(ValueError):accept_cache({},path,record,expected)
            with self.assertRaises(ValueError):accept_cache(config,path,record,dict(expected,execution={'risk':True}))
            path.write_text('{}')
            with self.assertRaises(ValueError):accept_cache(config,path,record,expected)

    def test_gpu_gate_keeps_graph_order_and_bounded_turns(self):
        from research.vectorized_backtest.v56.gpu_gate import ReplayGate,enqueue_turns
        from types import SimpleNamespace
        events=[];progress=[]
        gate=ReplayGate('bounded-fixture')
        try:
            with patch('torch.cuda.current_stream',return_value=SimpleNamespace(synchronize=lambda:events.append('sync'))):
                enqueue_turns(SimpleNamespace(replay=lambda:events.append('graph')),513,32,gate,'cuda',lambda a,b:progress.append((a,b)))
            self.assertEqual(events.count('graph'),17);self.assertEqual(events.count('sync'),3)
            self.assertEqual(progress,[(256,513),(512,513),(513,513)])
            with self.assertRaisesRegex(RuntimeError,'fixture'):
                with gate.turn():raise RuntimeError('fixture')
            with gate.turn():pass
        finally:gate.close()

    def test_runpy_entry_submits_importable_worker(self):
        import runpy
        namespace=runpy.run_module('research.vectorized_backtest.v56.campaign_runner',run_name='scheduled_entry_fixture')
        run=namespace['run'];run.__globals__.update(check_job=lambda job:None,stop_check=lambda roots:None)
        with TemporaryDirectory(dir=RUNTIME) as tmp:
            # Only the parent skips this fixture stop marker. The real spawned
            # worker must reach stop_check, proving serialization through runpy.
            (Path(tmp)/'STOP').write_text('fixture')
            job=dict(campaign=tmp,transport=tmp,machines={'workstation':dict(session_days=['one','two'],session_workers=2)})
            with self.assertRaises(InterruptedError):run(job,'workstation')

    def test_continuation_chain_keeps_original_export_authority(self):
        from research.vectorized_backtest.v56.resume_authority import source_allowed,export_job
        old={k:'same' for k in ('training_sha256','validation_sha256','objective','execution','selection','population','initial_multiplier','seed','phase1_generations','phase2_generations','ticker_parent_fraction','batch_size','asset_sha256')}
        old.update(training_days=['day'],validation_days=['valid'],source_sha256='original')
        with TemporaryDirectory(dir=RUNTIME) as tmp:
            p=Path(tmp)/'original.json';write_json(p,old)
            mid=dict(old,source_sha256='middle',resume_transition=dict(predecessor_job=str(p),predecessor_job_sha256=sha(p),exports={'day':'receipt'}))
            m=Path(tmp)/'middle.json';write_json(m,mid)
            top=dict(old,source_sha256='new',resume_transition=dict(predecessor_job=str(m),predecessor_job_sha256=sha(m),exports={'day':'receipt'}))
            self.assertTrue(source_allowed(top,'original'));self.assertTrue(source_allowed(top,'middle'))
            self.assertEqual(export_job(top,'day',dict(job_sha256=digest(old))),old)
            top['resume_transition']['exports']['day']='changed'
            with self.assertRaises(ValueError):export_job(top,'day',dict(job_sha256=digest(old)))

if __name__=='__main__':unittest.main()
