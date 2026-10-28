import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import json,unittest,threading
from pathlib import Path
from tempfile import TemporaryDirectory
import numpy as np
from research.vectorized_backtest.v56.generation_pipeline import overlap
from research.vectorized_backtest.v56.evolution_report import publish
from research.vectorized_backtest.v56.completed_replays import restore
from research.vectorized_backtest.v42.distributed_assets import sha
RUNTIME=Path('D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v56/tests');RUNTIME.mkdir(parents=True,exist_ok=True)

class TransitionTests(unittest.TestCase):
    def test_validation_runs_while_reproduction_waits_and_cannot_change_rng(self):
        entered=threading.Event();validation=threading.Event()
        def reproduce():
            rng=np.random.default_rng(2021);entered.set()
            self.assertTrue(validation.wait(3))
            return rng.integers(100,size=10).tolist()
        def replay():
            self.assertTrue(entered.wait(3));validation.set();return {'display_only':999999}
        result,metrics,timing=overlap(reproduce,replay,True)
        self.assertEqual(result,np.random.default_rng(2021).integers(100,size=10).tolist())
        self.assertTrue(timing['concurrent']);self.assertEqual(metrics,{'display_only':999999})

    def test_reproduction_failure_propagates(self):
        def fail():raise ValueError('report failed')
        with self.assertRaisesRegex(ValueError,'report failed'):overlap(fail,lambda:None,True)

    def test_numpy_parent_comparisons_publish_strict_json(self):
        with TemporaryDirectory(dir=RUNTIME) as tmp:
            root=Path(tmp);folder=root/'g';folder.mkdir()
            records=[dict(serial=1,global_founder=True,operation='session-expert'),dict(serial=2,global_founder=True,operation='blind-reproduction',blind_mix=dict(method='union',parent_serials=[1]))]
            panel={k:np.array([1.,2.]) for k in ('score','net_pnl','gross_losses','drawdown','closed_positions')};panel['eligible']=np.ones(2,bool)
            report=publish(root,folder,np.array([1,2]),panel,records,{2},{},1)
            self.assertIs(report['candidates'][1]['beat_best_parent'],True)
            self.assertTrue(json.loads((folder/'founder-results.json').read_bytes())['2']['beat_best_parent'])

    def test_exact_completed_receipt_reuse_and_tamper_rejection(self):
        with TemporaryDirectory(dir=RUNTIME) as tmp:
            out=Path(tmp);p=out/'generation-0001/day/receipt.json';p.parent.mkdir(parents=True)
            p.write_text(json.dumps(dict(day='day',split='training',population_sha256='token',full_session=True,execution={},validation_opened=False,case_owners=[None],metrics={'net_pnl':[2]})))
            job=dict(execution={},resume_transition={'completed_replays':{'training/1/day':sha(p)}})
            result,refs=restore(job,out,'training',['day'],1,'token',1);self.assertEqual(result[0]['net_pnl'],[2])
            with self.assertRaises(ValueError):restore(job,out,'training',['day'],1,'different',1)
            p.write_text(p.read_text()+' ')
            with self.assertRaises(ValueError):restore(job,out,'training',['day'],1,'token',1)

    def test_status_publication_retries_transient_windows_lock(self):
        from unittest.mock import patch
        from research.vectorized_backtest.v56.campaign_runner import Evolution
        with TemporaryDirectory(dir=RUNTIME) as tmp:
            e=Evolution({'phase2_generations':50},'workstation',tmp,2,[])
            with patch('research.vectorized_backtest.v56.campaign_runner.write_json',side_effect=[PermissionError('locked'),None]) as writer,patch('research.vectorized_backtest.v56.campaign_runner.time.sleep'):
                e.publish(reproduction_stage='active');self.assertEqual(writer.call_count,2)

    def test_compiled_behavior_matches_broadcast_reference(self):
        from research.vectorized_backtest.v56.behavior_sketch import sketch
        rng=np.random.default_rng(9);entries=[[],[0,1799,1800,86399,90000]]+[rng.integers(0,100000,size=35).tolist() for _ in range(5)];exits=[[e+int(rng.integers(-100,6000)) for e in row] for row in entries];listings=[[int(rng.integers(0,1000)) for e in row] for row in entries];n=len(entries)
        m=dict(episode_entry_clocks=entries,episode_exit_clocks=exits,episode_listings=listings,closed_positions=list(map(len,entries)),capital_dollar_seconds=list(range(n)),turnover=list(range(n)))
        result,active=sketch(m,n);reference=np.zeros((n,131));lower=np.arange(48)*1800
        for i in range(n):
            e=np.asarray(entries[i],np.int64);x=np.asarray(exits[i],np.int64);l=np.asarray(listings[i],np.int64)
            reference[i,:48]=np.bincount(np.minimum(e//1800,47),minlength=48);reference[i,48:96]=np.maximum(0,np.minimum(x[:,None],lower+1800)-np.maximum(e[:,None],lower)).sum(0)/1800;reference[i,96:128]=np.bincount(l%32,minlength=32)
            reference[i,128:]=[len(e),i/3600000,i/1000]
        self.assertTrue(np.array_equal(result,np.log1p(reference)));self.assertEqual(active.tolist(),[bool(row) for row in entries])
