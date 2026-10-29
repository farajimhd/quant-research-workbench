import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import numpy as np


class GlobalTests(unittest.TestCase):
    def test_indexed_cache_roundtrip_and_receipt_tamper(self):
        import tempfile,json
        from research.vectorized_backtest.v57.publication_lane import publish_replays
        from research.vectorized_backtest.v57.prefetch_pipeline import read_results
        root=Path('D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v57/cache-contract-tests');root.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as tmp:
            receipt=str(Path(tmp)/'receipt.json');cache=str(Path(tmp)/'cache.json');identity={'source':'test'}
            results={'a':{'net_pnl':12.,'episode_profits':[12.]},'b':{'net_pnl':-2.,'episode_profits':[-2.]}}
            publish_replays([(cache,dict(identity=identity,results=results)),(receipt,dict(result_identity=identity,program_keys=['a','b'],metrics=dict(net_pnl=[12.,-2.],episode_profits=[[12.],[-2.]],result_reused=[False,False])))],True)
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
        result={'writes':[(path,receipt)]}
        c.session_result('2026-08-13',result,'training',1)
        self.assertEqual(result['metrics']['net_pnl'],[1,2]);self.assertEqual(result['diagnostic']['net_pnl'],[999])
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
