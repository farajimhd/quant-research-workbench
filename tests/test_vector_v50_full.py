import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import io,json,unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from types import SimpleNamespace
from rich.console import Console
from research.vectorized_backtest.v50.full_common import check_job
from research.vectorized_backtest.v50.full_run import FullRun
from research.vectorized_backtest.v50.identity import digest
from research.vectorized_backtest.v50.progress import render
from research.vectorized_backtest.v42.distributed_assets import sha
from tests.test_vector_v50_pilot import members,metrics

RUNTIME=Path('D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v50/full-preparation/tests')

class FullTests(unittest.TestCase):
    def setUp(self):RUNTIME.mkdir(parents=True,exist_ok=True)
    def test_full_job_only_workstation_and_complete_certified_splits(self):
        with TemporaryDirectory(dir=RUNTIME) as tmp:
            root=Path(tmp);train=[f'2026-01-{i:02d}' for i in range(1,25)];valid=[f'2026-02-{i:02d}' for i in range(1,13)]
            paths={}
            for name,data in [('training',dict(days=train)),('validation',dict(days=valid)),('source_job',{}),('initialization_probe',{})]:
                path=root/(name+'.json');path.write_text(json.dumps(data));paths[name]=str(path);paths[name+'_sha256']=sha(path)
            job=dict(paths,version='v50-full-v1',source_sha256='source',training_days=train,validation_days=valid,population=1024,initial_multiplier=6,phase2_generations=100,objective=dict(drawdown_weight=1.,loss_weight=.1),machines={'workstation':dict(days=dict(training=train,validation=valid))})
            with patch('research.vectorized_backtest.v50.full_common.source_hash',return_value='source'):
                check_job(job)
                with self.assertRaises(ValueError):check_job(dict(job,machines=dict(job['machines'],laptop={})))
                with self.assertRaises(ValueError):check_job(dict(job,training_days=train[:-1]))
                with self.assertRaises(ValueError):check_job(dict(job,phase2_generations=50))
                with self.assertRaises(ValueError):check_job(dict(job,objective=dict(drawdown_weight=.2,loss_weight=.5)))

    def test_sequential_role_replays_clear_resident_identity_and_bind_receipts(self):
        with TemporaryDirectory(dir=RUNTIME) as tmp:
            root=Path(tmp);history=root/'history';calls=[];execution=dict(price_mode='bid-ask')
            c=FullRun.__new__(FullRun);c.root=root;c.job=dict(training_days=['t1','t2'],validation_days=['v1','v2'],execution=execution);c.publish=lambda **kwargs:None
            for d in ['t1','t2','v1','v2']:
                (history/d).mkdir(parents=True);(history/d/'complete.json').write_text(json.dumps(dict(listing_ids=[0])))
            class Worker:
                def __init__(self,role):self.role=role;self.config=dict(history=str(history),output=str(root/role));self.resident_results='old-session'
                def run(self,day,next_day,payload,g,token,width):
                    self_test.assertIsNone(self.resident_results);calls.append((self.role,day));self.resident_results='new-session'
                    p=Path(self.config['output'])/f'generation-{g:04d}'/day/'receipt.json';p.parent.mkdir(parents=True)
                    p.write_text(json.dumps(dict(population_sha256=token,full_session=True,split=self.role,validation_opened=self.role=='validation',execution=execution,case_owners=[None]*len(payload),metrics=metrics(10,len(payload)))))
            self_test=self;workers={role:Worker(role) for role in ['training','validation']};c.get_worker=lambda role:workers[role]
            with patch('research.vectorized_backtest.v50.full_run.ensure_day') as ensure:
                for role in workers:
                    data,receipts=c.evaluate(role,c.job[role+'_days'],members(10),1);self.assertEqual(len(data),2);self.assertEqual(len(receipts),2)
                self.assertEqual(ensure.call_count,4)
            self.assertEqual(calls,[('training','t1'),('training','t2'),('validation','v1'),('validation','v2')])
            with self.assertRaises(ValueError):c.evaluate('training',['v1'],members(10),1)
            (root/'STOP').write_text('user stop')
            with self.assertRaises(InterruptedError):c.evaluate('validation',['v1','v2'],members(10),1)

    def test_full_terminal_labels_budgets_and_validation(self):
        state=dict(run_kind='full',status='running',total_generations=100,evaluation_split='validation',current_day='2026-09-03',completed_sessions=1,total_sessions=12)
        for width,height in [(120,38),(80,24)]:
            for validation in [False,True]:
                stream=io.StringIO();Console(file=stream,width=width,height=height,color_system=None).print(render(state,width,height,validation=validation))
                text=stream.getvalue();self.assertEqual(len(text.splitlines()),height);self.assertTrue(all(len(x)<=width for x in text.splitlines()));self.assertIn('workstation',text);self.assertIn('0/100',text);self.assertIn('1/12',text)
                if validation:self.assertIn('VALIDATION: DISPLAY ONLY',text)

if __name__=='__main__':unittest.main()
