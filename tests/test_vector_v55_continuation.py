import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import numpy as np
from tests.test_vector_v53_pilot import metrics
def members(n):
    from research.vectorized_backtest.v55.genome import sample
    return sample(np.random.default_rng(2021),n)
from research.vectorized_backtest.v55.full_controller import Controller, checkpoint_contract
from research.vectorized_backtest.v55.continuation import boundary_state, verify
from research.vectorized_backtest.v55.full_common import aggregate
from research.vectorized_backtest.v55.objectives import Objective
from research.vectorized_backtest.v55.identity import digest
from research.vectorized_backtest.v55.genome import sample
from research.vectorized_backtest.v42.publication import write_checkpoint


class ContinuationTests(unittest.TestCase):
    def controller(self, root, generations):
        c = Controller.__new__(Controller)
        c.root = Path(root)
        c.publish = lambda **values: None
        c.job = dict(population=10, initial_multiplier=3, seed=2021,
            phase2_generations=generations, training='fixture', initialization_probe='fixture',
            source_sha256='source', objective=dict(drawdown_weight=2.,loss_weight=1.),
            training_sha256='training', validation_sha256='validation', selection={})
        c.calls = []
        def evaluate(role, days, pop, g):
            c.calls.append(g)
            return [metrics(10., len(pop))], {}
        c.evaluate = evaluate
        return c

    def test_terminal_extension_matches_uninterrupted_evolution_without_replaying_boundary(self):
        root = Path('D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v55/preparation/tests')
        root.mkdir(parents=True,exist_ok=True)
        with TemporaryDirectory(dir=root) as uninterrupted, TemporaryDirectory(dir=root) as extended, \
             patch('research.vectorized_backtest.v42.calibration.load_probe',return_value={}), \
             patch('research.vectorized_backtest.v55.full_seed.initialize',
                   return_value=(members(30),dict(initial_families=['random']*30))), \
             patch('research.vectorized_backtest.v55.initial_population.immigrant',
                   side_effect=lambda rng,probe:sample(rng,1)[0]):
            baseline = self.controller(uninterrupted, 5)
            baseline.global_run({}, ['2026-08-13'], [])
            c = self.controller(extended, 2)
            c.global_run({}, ['2026-08-13'], [])
            checkpoint = c.root/'global/checkpoint.json'
            saved = json.loads(checkpoint.read_bytes())
            old_state = digest(saved['state'])
            ordered = [metrics(10.,10)]
            panel = aggregate(ordered,Objective(**c.job['objective']))
            state, births, telemetry = boundary_state(c.job,saved['state'],ordered,panel,{})
            self.assertEqual(digest(saved['state']),old_state)
            self.assertTrue(all(r['generation']==3 for r in births))
            self.assertEqual(state['completed'],2)
            self.assertEqual(state['seals'],saved['state']['seals'])
            c.job['phase2_generations'] = 5
            write_checkpoint(checkpoint,checkpoint_contract(c.job,['2026-08-13'],[]),state)
            c.calls.clear()
            c.global_run({},['2026-08-13'],[])
            self.assertEqual(c.calls,[3,4,5])
            actual = json.loads(checkpoint.read_bytes())['state']
            expected = json.loads((baseline.root/'global/checkpoint.json').read_bytes())['state']
            keys = ('population','ids','rng','pedigree','next_serial','species')
            self.assertEqual(digest({k:actual[k] for k in keys}),digest({k:expected[k] for k in keys}))

    def test_proof_hash_mismatch_fails_before_opening_context(self):
        with patch('research.vectorized_backtest.v55.continuation.sha',return_value='wrong'):
            with self.assertRaisesRegex(ValueError,'context hash'):
                verify(dict(continuation=dict(context='fixture',context_sha256='expected')))

    def test_missing_proof_is_rejected(self):
        with self.assertRaisesRegex(ValueError,'proof required'):
            verify({})

    def test_budget_contract_binds_the_transition(self):
        c = self.controller('unused',50)
        c.job['continuation'] = dict(context_sha256='proof')
        contract = checkpoint_contract(c.job,['2026-08-13'],[])
        self.assertEqual(contract['generations'],50)
        self.assertEqual(contract['continuation_sha256'],'proof')


if __name__ == '__main__':
    unittest.main()
