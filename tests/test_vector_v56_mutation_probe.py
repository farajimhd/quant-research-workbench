import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
from tests.test_vector_v53_pilot import metrics as base_metrics

def metrics(value,n):
    m=base_metrics(value,n);m["ticker_score"]=[{"a":float(value)} for _ in range(n)];return m
def members(n):
    from research.vectorized_backtest.v56.genome import sample
    return sample(np.random.default_rng(2021),n)
from research.vectorized_backtest.v56.full_common import aggregate
from research.vectorized_backtest.v56.identity import digest
from research.vectorized_backtest.v56.mutation_probe import run_probe


def seed_fixture(exports,job,probe):
    from research.vectorized_backtest.v56.local_runner import CompactPedigree
    pop=members(30);pedigree=CompactPedigree(3);ids=pedigree.birth(pop,1,[(-1,-1)]*30,['fresh']*30)
    return dict(population=[m.payload() for m in pop],ids=ids.tolist(),pedigree=list(pedigree.records.values()),next_serial=pedigree.next_serial,rng=np.random.default_rng(job['seed']).bit_generator.state,initialization={})

class MutationProbeTests(unittest.TestCase):
    def test_real_controller_successor_and_rng_are_identical_with_probe_enabled(self):
        from research.vectorized_backtest.v56.full_controller import Controller
        from research.vectorized_backtest.v56.genome import sample
        runtime = Path('D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v56/preparation/tests')
        runtime.mkdir(parents=True, exist_ok=True)
        outcomes = []
        for size in (0, 32):
            with TemporaryDirectory(dir=runtime) as tmp:
                c = Controller.__new__(Controller)
                c.root = Path(tmp)
                c.publish = lambda **values: None
                c.worker = SimpleNamespace(config={'output': 'main'}, resident_results=None)
                c.job = dict(population=10, initial_multiplier=3, seed=2021,
                    phase2_generations=3, training='fixture', initialization_probe='fixture',
                    source_sha256='source', objective=dict(drawdown_weight=0., loss_weight=0.),
                    training_sha256='training', validation_sha256='validation', selection={},
                    mutation_probe_size=size, training_days=['2026-08-13'])
                c.evaluate = lambda role,days,pop,g: ([metrics(10.,len(pop))], {})
                with patch('research.vectorized_backtest.v42.calibration.load_probe',return_value={}), \
                     patch('research.vectorized_backtest.v56.full_seed.global_seed',side_effect=seed_fixture), \
                     patch('research.vectorized_backtest.v56.initial_population.immigrant',
                           side_effect=lambda rng,probe:sample(rng,1)[0]):
                    c.global_run({}, ['2026-08-13'], [])
                state = json.loads((c.root/'global/checkpoint.json').read_bytes())['state']
                outcomes.append(digest({k:state[k] for k in ('population','ids','rng','pedigree')}))
                if size:
                    probes = [json.loads(p.read_bytes()) for p in (c.root/'global').glob('generation-*/mutation-probe.json')]
                    self.assertGreater(sum(p['evaluated'] for p in probes),0)
        self.assertEqual(*outcomes)

    def test_paired_diagnostic_isolates_output_cache_rng_and_cohort(self):
        runtime = Path('D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v56/preparation/tests')
        runtime.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=runtime) as tmp:
            pop = members(5)
            frozen = digest([m.payload() for m in pop])
            resident = ('main-identity', {'main-program': 'main-result'})
            worker = SimpleNamespace(config={'output': 'main-replay'}, resident_results=resident)
            c = SimpleNamespace(root=Path(tmp), worker=worker,
                job=dict(seed=2021, mutation_probe_size=32, training_days=['2026-08-13'],
                         objective=dict(loss_weight=0., drawdown_weight=0.)))
            records = [dict(generation=2, operation='crossover', whole_network=False)] * 5
            ids = np.arange(5)
            calls = []
            def evaluate(role, days, mutants, generation):
                self.assertEqual(worker.config['output'], str(c.root/'mutation-probe-replay'))
                self.assertIsNone(worker.resident_results)
                self.assertEqual((role, days, generation), ('training', ['2026-08-13'], 2))
                calls.append([m.payload() for m in mutants])
                worker.resident_results = ('diagnostic', {})
                return [metrics(12., len(mutants))], {}
            c.evaluate = evaluate
            panel = aggregate([metrics(10., 5)])
            with patch('research.vectorized_backtest.v56.mutation_probe.mutation',
                       side_effect=lambda rng,m,edits,cross:(members(7)[int(rng.integers(7))], [])):
                run_probe(c, c.root, pop, ids, records, panel, 2)
            result = json.loads((c.root/'mutation-probe.json').read_bytes())
            self.assertEqual(result['requested'], 5)
            self.assertEqual(result['evaluated'], 5)
            self.assertEqual(len(calls), 1)
            self.assertEqual(worker.config['output'], 'main-replay')
            self.assertIs(worker.resident_results, resident)
            self.assertFalse(result['training_rng_used'])
            self.assertEqual(frozen, digest([m.payload() for m in pop]))
            self.assertTrue(all(p['baseline']['net_pnl']==10. and p['mutated']['net_pnl']==12.
                                for p in result['pairs']))

    def test_diagnostic_failure_restores_main_worker(self):
        runtime = Path('D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v56/preparation/tests')
        runtime.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=runtime) as tmp:
            pop = members(2)
            resident = ('main', {})
            worker = SimpleNamespace(config={'output': 'main'}, resident_results=resident)
            c = SimpleNamespace(root=Path(tmp),worker=worker,
                job=dict(seed=2021,mutation_probe_size=32,training_days=['2026-08-13']))
            def fail(*args):raise RuntimeError('replay failed')
            c.evaluate = fail
            records = [dict(generation=2,operation='crossover',whole_network=False)] * 2
            with patch('research.vectorized_backtest.v56.mutation_probe.mutation',
                       return_value=(members(3)[1], [])):
                with self.assertRaisesRegex(RuntimeError, 'replay failed'):
                    run_probe(c,c.root,pop,np.arange(2),records,aggregate([metrics(1.,2)]),2)
            self.assertEqual(worker.config['output'],'main')
            self.assertIs(worker.resident_results,resident)


if __name__ == '__main__':
    unittest.main()
