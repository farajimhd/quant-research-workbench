import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import unittest
import json,hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from research.vectorized_backtest.v56.immigration import select

class ImmigrationTests(unittest.TestCase):
    def test_disjoint_quotas_include_profit_leader_and_preserve_score_leader(self):
        ids=list(range(200));panel=dict(eligible=[True]*200,closed_positions=[1]*200,score=list(range(200)),net_pnl=list(reversed(range(200))))
        result=select(ids,panel,{i:str(i) for i in ids},policy='score75-net25-v1')
        self.assertEqual(len(result),128)
        self.assertEqual([r['index'] for r in result[:96]],list(range(199,103,-1)))
        self.assertEqual([r['index'] for r in result[96:]],list(range(32)))
        self.assertEqual(result[96]['net_rank'],1)
        self.assertEqual(result[0]['score_rank'],1)

    def test_duplicate_program_and_ineligible_profit_leader_are_excluded(self):
        ids=list(range(200));panel=dict(eligible=[True]*200,closed_positions=[1]*200,score=list(range(200)),net_pnl=list(reversed(range(200))))
        panel['eligible'][0]=False;hashes={i:str(i) for i in ids};hashes[1]=hashes[199]
        result=select(ids,panel,hashes,policy='score75-net25-v1')
        self.assertEqual(result[96]['index'],2)
        self.assertEqual(len({hashes[r['index']] for r in result}),128)

    def test_legacy_selection_remains_score_only(self):
        ids=list(range(150));panel=dict(eligible=[True]*150,closed_positions=[1]*150,score=ids,net_pnl=ids)
        self.assertEqual([r['index'] for r in select(ids,panel,{i:str(i) for i in ids})],list(range(149,21,-1)))

    def test_source_receipt_rows_are_copied_by_network_identity_and_tampering_fails(self):
        from research.vectorized_backtest.v56.immigration_cache import prepare
        base=Path('D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v56/qualification/immigration');base.mkdir(parents=True,exist_ok=True)
        with TemporaryDirectory(dir=base) as tmp:
            root=Path(tmp);folder=root/'sessions/day';folder.mkdir(parents=True)
            def save(name,value):
                data=json.dumps(value).encode();(folder/name).write_bytes(data);return hashlib.sha256(data).hexdigest()
            metrics=dict(net_pnl=[123],drawdown=[7],episode_profits=[[123]],result_reused=[False])
            receipt=dict(day='day',split='training',full_session=True,validation_opened=False,execution={},population_sha256='population',identity={'data':'frozen'},metrics=metrics)
            rh=save('receipt.json',receipt)
            th=save('training.json',dict(ids=[90],population_sha256='population',receipts={'day':{'receipt_sha256':rh}}))
            ph=save('pedigree.json',[dict(serial=90,program_sha256='network')])
            eh=save('export.json',dict(day='day',files={'receipt.json':rh,'training.json':th,'pedigree.json':ph}))
            job=dict(training_days=['day'],source_sha256='new',execution={},resume_transition={'exports':{'day':eh}})
            prepare(job,root,root/'output');cache=json.loads((root/'output/training/result-cache/day.json').read_bytes())
            self.assertEqual(cache['results']['network'],dict(net_pnl=123,drawdown=7,episode_profits=[123]))
            receipt['metrics']['net_pnl']=[999];save('receipt.json',receipt)
            with self.assertRaisesRegex(ValueError,'evidence changed'):prepare(job,root,root/'other')
