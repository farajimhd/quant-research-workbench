import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
from tests.test_vector_v53_pilot import members, metrics
from research.vectorized_backtest.v53.full_common import aggregate
from research.vectorized_backtest.v53.identity import digest
from research.vectorized_backtest.v53.mutation_probe import run_probe


class MutationProbeTests(unittest.TestCase):
    def test_paired_diagnostic_isolates_output_cache_rng_and_cohort(self):
        runtime = Path('D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v53/preparation/tests')
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
            with patch('research.vectorized_backtest.v53.mutation_probe.mutation',
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
        runtime = Path('D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v53/preparation/tests')
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
            with patch('research.vectorized_backtest.v53.mutation_probe.mutation',
                       return_value=(members(3)[1], [])):
                with self.assertRaisesRegex(RuntimeError, 'replay failed'):
                    run_probe(c,c.root,pop,np.arange(2),records,aggregate([metrics(1.,2)]),2)
            self.assertEqual(worker.config['output'],'main')
            self.assertIs(worker.resident_results,resident)


if __name__ == '__main__':
    unittest.main()
