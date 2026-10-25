import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import io,json,unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import numpy as np
from rich.console import Console
from tests.test_vector_v51_whole_network import parent
from research.vectorized_backtest.v51.full_controller import Controller
from research.vectorized_backtest.v51.full_common import aggregate
from research.vectorized_backtest.v51.objectives import Objective
from research.vectorized_backtest.v51.identity import digest
from research.vectorized_backtest.v51.progress import render
from research.vectorized_backtest.v51.genome import sample


def members(n):
    original=parent(0)
    return [replace(original,networks={s:replace(net,threshold=-.8+1.6*i/n,size_bias=-.7+1.4*i/n) for s,net in original.networks.items()}) for i in range(n)]

def metrics(value,n):
    return dict(net_pnl=[float(value)]*n,drawdown=[2.]*n,closed_positions=[1]*n,winning_positions=[int(value>0)]*n,
        episode_profits=[[float(value)]]*n,episode_entry_clocks=[[1]]*n,episode_listings=[[0]]*n,episode_exit_clocks=[[2]]*n,terminal_valid=[True]*n,
        maximum_position_risk=[10.]*n,maximum_stop_distance=[.1]*n,capital_dollar_seconds=[100.]*n,turnover=[1000.]*n)

class PilotTests(unittest.TestCase):
    def test_new_cost_open_equity_and_loss_penalties(self):
        self.assertEqual(Objective(),Objective(drawdown_weight=.05,loss_weight=.05))
        m=metrics(-10,1);m['drawdown']=[50.]
        result=aggregate([m]);self.assertEqual(result['score'].tolist(),[-13.])
        result=aggregate([metrics(100,1),m]);self.assertEqual(result['score'].tolist(),[86.9])

    def test_fresh_seed_no_archived_experts_and_sixfold_founders(self):
        from research.vectorized_backtest.v51.full_seed import global_seed
        job=dict(population=10,initial_multiplier=6,seed=2020,source_sha256='source')
        with patch('research.vectorized_backtest.v51.full_seed.initialize',return_value=(members(60),dict(initial_families=['random']*60))):
            seed=global_seed({},job,{})
        self.assertEqual(len(seed['population']),60);self.assertEqual(seed['expert_count'],0)
        self.assertEqual(len(seed['pedigree']),60);self.assertTrue(all(r['random_ancestry']==1 for r in seed['pedigree']))
        with self.assertRaisesRegex(ValueError,'prior session'):global_seed({'old':{}},job,{})

    def test_initialization_ignores_old_pool_environment(self):
        from research.vectorized_backtest.v51.initial_population import initialize
        templates=members(3072);randoms=members(3072)
        # Distinguish random programs semantically from all template programs.
        randoms=[replace(m,minimum_age={k:v+1 for k,v in m.minimum_age.items()}) for m in randoms]
        with patch.dict(os.environ,{'V42_INITIAL_POOL':'does-not-exist','V51_INITIAL_POOL':'does-not-exist'}),patch('research.vectorized_backtest.v42.initial_population.initialize_random',return_value=(randoms,{})),patch('research.vectorized_backtest.v42.templates.sample_templates',return_value=(templates,['random']*3072)):
            initial,report=initialize(np.random.default_rng(2020),6144,probe={})
        self.assertEqual(len(initial),6144);self.assertEqual(report['random_count'],3072);self.assertEqual(report['seed_count'],3072);self.assertFalse(report['past_results_loaded'])

    @patch('research.vectorized_backtest.v42.calibration.load_probe',return_value={})
    @patch('research.vectorized_backtest.v51.initial_population.immigrant',side_effect=lambda rng,probe:sample(rng,1)[0])
    def test_real_phase2_loop_reduces_population_resume_and_validation_rng(self,*mocks):
        runtime=Path('D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v51/preparation/tests');runtime.mkdir(parents=True,exist_ok=True)
        founders=members(30);hashes=[]
        for val in (20.,-100000.):
            with TemporaryDirectory(dir=runtime) as tmp:
                c=Controller.__new__(Controller);c.root=Path(tmp);c.stage='';c.publish=lambda **kw:None
                c.job=dict(population=10,initial_multiplier=3,seed=2020,phase2_generations=2,training='fixture',initialization_probe='fixture',source_sha256='source',objective=dict(drawdown_weight=.05,loss_weight=.05),training_sha256='training',validation_sha256='validation',selection={})
                calls=[]
                def evaluate(role,days,pop,g):
                    calls.append((role,g,len(pop)));return [metrics(10 if role=='training' else val,len(pop)) for _ in days],{}
                c.evaluate=evaluate
                with patch('research.vectorized_backtest.v51.full_seed.initialize',return_value=(founders,dict(initial_families=['random']*30))),patch('research.vectorized_backtest.v42.calibration.load_probe',return_value={}):
                    c.global_run({},['d1'],['v1'])
                self.assertEqual(calls,[('training',1,30),('validation',1,30),('training',2,10),('validation',2,10)])
                state=json.loads((c.root/'global/checkpoint.json').read_bytes())['state'];hashes.append(digest(dict(population=state['population'],ids=state['ids'],rng=state['rng'])))
                calls.clear();c.global_run({},['d1'],['v1']);self.assertEqual(calls,[])
                (c.root/'global/generation-0001/training.json').write_text('{}')
                with self.assertRaisesRegex(ValueError,'evidence'):c.global_run({},['d1'],['v1'])
        self.assertEqual(*hashes)
        with TemporaryDirectory(dir=runtime) as tmp:
            c.root=Path(tmp);calls.clear()
            with patch('research.vectorized_backtest.v51.full_seed.initialize',return_value=(founders,dict(initial_families=['random']*30))),patch('research.vectorized_backtest.v42.calibration.load_probe',return_value={}):c.global_run({},['d1'],[])
            self.assertEqual(calls,[('training',1,30),('training',2,10)])
            seal=json.loads((c.root/'global/generation-0001/validation.json').read_bytes());self.assertFalse(seal['opened']);self.assertEqual(seal['panel'],{});self.assertEqual(seal['successor_sha256_before'],seal['successor_sha256_after'])

    def test_pilot_refuses_other_sessions_validation_and_stop(self):
        from research.vectorized_backtest.v51.pilot import Pilot
        runtime=Path('D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v51/preparation/tests');runtime.mkdir(parents=True,exist_ok=True)
        with TemporaryDirectory(dir=runtime) as tmp:
            p=Pilot.__new__(Pilot);p.root=Path(tmp);p.job=dict(training_days=['2026-08-13'])
            with self.assertRaisesRegex(ValueError,'cannot open'):p.evaluate('validation',['2026-08-13'],[],1)
            with self.assertRaisesRegex(ValueError,'cannot open'):p.evaluate('training',['2026-08-14'],[],1)
            (p.root/'STOP').write_text('user requested')
            with self.assertRaises(InterruptedError):p.evaluate('training',['2026-08-13'],[],1)

    def test_console_bounds_and_cost_labels(self):
        for width,height in ((120,38),(80,24)):
            s=io.StringIO();Console(file=s,width=width,height=height,color_system=None).print(render({},width,height))
            lines=s.getvalue().splitlines();self.assertEqual(len(lines),height);self.assertTrue(all(len(line)<=width for line in lines));self.assertIn('V51',s.getvalue());self.assertIn('0.05 x gross losses',s.getvalue());self.assertIn('Q closes',s.getvalue())

if __name__=='__main__':unittest.main()
