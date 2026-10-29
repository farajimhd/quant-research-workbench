import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import numpy as np


class GlobalTests(unittest.TestCase):
    def test_saved_training_rows_publish_before_validation_ledger_load(self):
        from threading import Event
        from unittest.mock import patch
        from research.vectorized_backtest.v57.progress_sessions import load_global
        from research.vectorized_backtest.v57.full_common import aggregate
        m=dict(net_pnl=[5.],drawdown=[1.],closed_positions=[1.],winning_positions=[1.],maximum_position_risk=[1.],maximum_stop_distance=[.1],terminal_valid=[True],episode_profits=[[5.]],episode_exit_clocks=[[2]],episode_listings=[[0]])
        panel={k:v.tolist() for k,v in aggregate([m]).items()};events=[]
        def read(path,expected=None):
            name=Path(path).name
            if name=='complete.json':return dict(training_sha256='t',validation_sha256='v',observational_reports={'session-metrics.json':'s'})
            if name=='session-metrics.json':return dict(ids=[7],sessions={'train':panel})
            if name in ('training.json','validation.json'):
                role=name[:-5];day='train' if role=='training' else 'valid'
                return dict(ids=[7],population_sha256='pool',display_only=True,receipts={day:dict(receipt=day+'.json',receipt_sha256='r')})
            if name=='valid.json':
                self.assertEqual(len(events),1)
                return dict(day='valid',split='validation',population_sha256='pool',full_session=True,validation_opened=True,metrics=m)
            raise AssertionError('Unexpected full receipt read: '+name)
        job=dict(training_days=['train'],validation_days=['valid'],objective=dict(drawdown_weight=1.,loss_weight=1.))
        with patch('research.vectorized_backtest.v57.progress_sessions.read_verified',side_effect=read),patch.object(Path,'exists',return_value=False):
            result=load_global(job,Path('D:/TradingML/runtimes/observer-test'),1,Event(),partial=events.append)
        self.assertEqual(list(events[0]['summaries']),['training:train'])
        self.assertEqual(len(result['summaries']),2)
        self.assertNotIn('loading',result)

    def test_global_resume_binds_checkpoint_and_preserves_campaign(self):
        import tempfile,json,copy
        from research.vectorized_backtest.v57.resume_authority import accept_checkpoint
        from research.vectorized_backtest.v57.identity import digest
        from research.vectorized_backtest.v42.distributed_assets import sha
        root=Path('D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v57/resume-tests');root.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as tmp:
            root=Path(tmp);prior=root/'prior.json';cp=root/'checkpoint.json'
            old=dict(version='v57-global-v1',source_sha256='old',code='old',commit='old',selection={'rate':.25},machines={'workstation':{'code':'old'}})
            prior.write_text(json.dumps(old));saved=dict(contract={'source_sha256':'old','population':1024},state_sha256='state')
            cp.write_text(json.dumps(saved));job=copy.deepcopy(old);job.update(source_sha256='new',code='new',commit='new')
            job['machines']['workstation']['code']='new'
            job['resume_transition']=dict(predecessor_job=str(prior),predecessor_job_sha256=sha(prior),global_checkpoint=dict(checkpoint_sha256=sha(cp),state_sha256='state',contract_sha256=digest(saved['contract'])))
            expected=dict(saved['contract'],source_sha256='new');accept_checkpoint(job,cp,saved,expected)
            job['selection']['rate']=.5
            with self.assertRaisesRegex(ValueError,'semantics'):accept_checkpoint(job,cp,saved,expected)
            job['selection']['rate']=.25;cp.write_text('{}')
            with self.assertRaisesRegex(ValueError,'bound receipt'):accept_checkpoint(job,cp,saved,expected)

    def test_compact_controller_inputs_preserve_scores_behavior_and_specialists(self):
        from research.vectorized_backtest.v57.controller_metrics import compact
        from research.vectorized_backtest.v57.full_common import aggregate
        from research.vectorized_backtest.v57.behavior_sketch import sketch
        from research.vectorized_backtest.v57.ticker_selection import specialist_pools
        m=dict(net_pnl=[10.,-3.],drawdown=[5.,7.],closed_positions=[2.,1.],winning_positions=[1.,0.],
            maximum_position_risk=[1.,2.],maximum_stop_distance=[.1,.2],terminal_valid=[True,True],
            capital_dollar_seconds=[100.,200.],turnover=[300.,400.],episode_profits=[[12.,-2.],[-3.]],
            episode_entry_clocks=[[1,4],[3]],episode_exit_clocks=[[3,8],[7]],episode_listings=[[0,0],[1]],
            ticker_score=[{'a':-4.},{'b':-6.}],unused_display_field=[[1],[2]])
        c=compact(m)
        for k,v in aggregate([m,m]).items():np.testing.assert_array_equal(v,aggregate([c,c])[k])
        for a,b in zip(sketch(m,2),sketch(c,2)):np.testing.assert_array_equal(a,b)
        args=(np.ones(2,bool),np.ones(2,bool),np.arange(2))
        self.assertEqual(specialist_pools([m],*args),specialist_pools([c],*args))
        self.assertIsInstance(m['episode_profits'][0],list)
        self.assertFalse(c['episode_profits'][0].flags.writeable)

    def test_positions_available_before_generation_seal(self):
        import tempfile,json
        from research.vectorized_backtest.v57.position_details import load
        root=Path('D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v57/position-tests');root.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as tmp:
            root=Path(tmp);folder=root/'global/generation-0001';folder.mkdir(parents=True)
            token='a'*64
            (folder/'population.json').write_text(json.dumps(dict(ids=[17],population=[],population_sha256=token),separators=(',',':')))
            path=root/'replays/validation/generation-0001/2026-09-02/receipt.json';path.parent.mkdir(parents=True)
            receipt=dict(population_sha256=token,day='2026-09-02',split='validation',full_session=True,validation_opened=True,
                listing_ids=[42],metrics=dict(episode_profits=[[7.]],episode_listings=[[0]],episode_entry_clocks=[[2]],episode_exit_clocks=[[8]]))
            path.write_text(json.dumps(receipt))
            rows=load(root,1,17,'validation:2026-09-02','unused')
            self.assertEqual(rows[0]['net_pnl'],7.)
            self.assertEqual(rows[0]['evidence'],'published replay; generation unsealed')
            receipt['population_sha256']='b'*64;path.write_text(json.dumps(receipt))
            with self.assertRaisesRegex(ValueError,'identity mismatch'):load(root,1,17,'validation:2026-09-02','unused')

    def test_concurrent_validation_progress_uses_explicit_split(self):
        from unittest.mock import patch
        from research.vectorized_backtest.v57.campaign_progress import Model,replay_numbers
        model=object.__new__(Model)
        model.job={'training_days':['2026-08-13'],'validation_days':['2026-09-02']}
        model.global_root=lambda:Path('D:/TradingML/runtimes/progress-fixture')
        model.replay_sample_scope=None;model.replay_samples={}
        model.read=Mock(return_value=dict(completed_generations=0,
            stage='Validation replay + training-only reproduction',replay_generation=1,replay_role='validation',
            active_sessions=['2026-09-02'],session_paths={'2026-09-02':dict(
                completed_candidates=1024,total_candidates=2048,completed_seconds=50,total_seconds=100,batch_candidates=1024)}))
        with patch.object(Path,'exists',lambda path:path.name=='population.json'):
            rows=model.global_replays()
        self.assertEqual(rows['validation:2026-09-02']['status'],'running')
        self.assertEqual(replay_numbers(rows['validation:2026-09-02'])['fraction'],.75)
        self.assertEqual(rows['training:2026-08-13']['status'],'queued')

    def test_transition_cache_reuses_layout_not_tensor_storage(self):
        import torch
        from unittest.mock import patch
        from research.vectorized_backtest.v57 import replay_graph as graph
        graph._TRANSITIONS.clear()
        try:
            with patch.object(graph,'scoped_compile',side_effect=lambda *a,**kw:object()) as compile:
                first=graph.transition_program((torch.zeros(2),1),False)
                self.assertIs(first,graph.transition_program((torch.ones(2),1),False))
                self.assertIsNot(first,graph.transition_program((torch.zeros(3),1),False))
                self.assertEqual(compile.call_count,2)
                for n in range(40):graph.transition_program((torch.zeros(n+4),1),False)
                self.assertEqual(len(graph._TRANSITIONS),32)
        finally:graph._TRANSITIONS.clear()

    def test_prefetch_failure_propagates_before_replay_dispatch(self):
        from concurrent.futures import Future
        from research.vectorized_backtest.v57.session_lanes import SessionLanes
        lanes=object.__new__(SessionLanes);future=Future();future.set_exception(ValueError('bad prefetched authority'))
        lanes.preload_futures=[future]
        with self.assertRaisesRegex(ValueError,'bad prefetched authority'):lanes.generation([],[],1,'token')

    def test_indexed_cache_roundtrip_and_receipt_tamper(self):
        import tempfile,json
        from research.vectorized_backtest.v57.publication_lane import publish_replays
        from research.vectorized_backtest.v57.prefetch_pipeline import read_results
        root=Path('D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v57/cache-contract-tests');root.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as tmp:
            receipt=str(Path(tmp)/'receipt.json');cache=str(Path(tmp)/'cache.json');identity={'source':'test'}
            results={'a':{'net_pnl':12.,'episode_profits':[12.]},'b':{'net_pnl':-2.,'episode_profits':[-2.]}}
            publish_replays([(cache,dict(identity=identity,results=results)),(receipt,dict(result_identity=identity,program_keys=['a','b'],metrics=dict(net_pnl=[12.,-2.],episode_profits=[[12.],[-2.]],result_reused=[False,False]))),(str(Path(tmp)/'timings.json'),[{'seconds':1.}])],True)
            self.assertNotIn('results',json.loads(Path(cache).read_bytes()))
            self.assertEqual(read_results(cache)[1]['results'],results)
            Path(receipt).write_text('{}')
            with self.assertRaises(ValueError):read_results(cache)

    def test_paired_session_keeps_training_and_diagnostics_separate(self):
        from research.vectorized_backtest.v57.global_runner import Global
        c=object.__new__(Global);c.root=Path('D:/TradingML/runtimes/test-global')
        c.job={'execution':{'risk_limits_enabled':False}};c.active_request=(2,'main','combined')
        c.paired_probe={'mutants':[object()]};c.diagnostic_token='diagnostic';c.published_receipts=[]
        c.publication_lane=SimpleNamespace(submit=Mock())
        path=str(c.root/'replays/training/generation-0001/2026-08-13/receipt.json')
        receipt=dict(population_sha256='combined',split='training',validation_opened=False,full_session=True,execution=c.job['execution'],case_owners=[None]*3,
                     metrics={'net_pnl':[1,2,999],'result_reused':[False]*3},program_keys=['a','b','c'])
        from research.vectorized_backtest.v57.controller_metrics import SCALARS,LEDGERS
        for key in SCALARS:receipt['metrics'].setdefault(key,[0]*3)
        for key in LEDGERS:receipt['metrics'][key]=[[],[],[]]
        result={'writes':[(path,receipt)]}
        c.session_result('2026-08-13',result,'training',1)
        np.testing.assert_array_equal(result['metrics']['net_pnl'],[1,2]);np.testing.assert_array_equal(result['diagnostic']['net_pnl'],[999])
        self.assertNotIn('writes',result)
        writes=c.publication_lane.submit.call_args.args[1]
        self.assertEqual(writes[0][1]['population_sha256'],'combined')
        self.assertEqual(writes[1][1]['population_sha256'],'main')
        self.assertEqual(writes[2][1]['population_sha256'],'diagnostic')
        self.assertEqual(writes[1][1]['program_keys'],['a','b']);self.assertEqual(writes[2][1]['program_keys'],['c'])

    def test_large_execution_batch_keeps_order_and_workspace_admission(self):
        from research.vectorized_backtest.v57.replay_batches import plan_batches
        member=SimpleNamespace(networks={'entry':SimpleNamespace(nodes=[None]*10)})
        population=[member]*6144;indices=list(range(6144))
        batches=plan_batches(population,indices,20,512,2048)
        self.assertEqual([len(b) for b in batches],[2048]*3)
        self.assertEqual(sum(batches,[]),indices)
        bounded=plan_batches(population,indices,20,512,2048,workspace_bytes=1)
        self.assertTrue(all(len(b)<=512 for b in bounded));self.assertEqual(sum(bounded,[]),indices)

    def test_validation_can_run_while_reproduction_waits_without_feedback(self):
        from threading import Event
        from research.vectorized_backtest.v57.generation_pipeline import overlap
        entered=Event();replayed=Event();rng=np.random.default_rng(2021)
        def reproduce():
            entered.set()
            if not replayed.wait(2):raise AssertionError('Validation blocked behind reproduction')
            return rng.integers(100000)
        def replay():
            self.assertTrue(entered.wait(2));replayed.set();return {'display_only':999999}
        result,validation,timing=overlap(reproduce,replay,True)
        self.assertEqual(result,np.random.default_rng(2021).integers(100000))
        self.assertTrue(timing['concurrent']);self.assertEqual(validation['display_only'],999999)


if __name__=='__main__':unittest.main()
