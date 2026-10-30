"""Initial founder restart preserves every genome and resets only lifecycle state."""
import copy
import unittest
from unittest.mock import patch
from research.vectorized_backtest.v59.execution_restart import seed
from research.vectorized_backtest.v59.initial_progress import InitialData


class InitialRestartTests(unittest.TestCase):
    def test_all_founders_preserved_and_generation_one_follows_initial(self):
        state=dict(population=[{'genome':i} for i in range(6144)],ids=list(range(6144)),
                   pedigree=[dict(serial=i,generation=1) for i in range(6144)],
                   completed=0,seals={},species={},rng={'state':123},next_serial=6144)
        original=copy.deepcopy(state)
        job={'execution_restart':{'mode':'initial-founders'}}
        with patch('research.vectorized_backtest.v59.execution_restart.verify',return_value={'state':state}):
            result=seed(job)
        for key in ('population','ids','rng','next_serial'):
            self.assertEqual(result[key],original[key])
        self.assertEqual(state,original)
        self.assertEqual(list(range(result['completed']+1,201)),list(range(201)))
        self.assertTrue(all(r['generation']==0 for r in result['pedigree']))
        self.assertEqual(InitialData(job,'.',None).generation,0)

    def test_checkpoint_continuation_retains_generation(self):
        state=dict(completed=6,seals={'6':'old'},species={},pedigree=[{'generation':6}])
        with patch('research.vectorized_backtest.v59.execution_restart.verify',return_value={'state':state}):
            result=seed({'execution_restart':{}})
        self.assertEqual(result['completed'],6)
        self.assertEqual(result['pedigree'],state['pedigree'])
        self.assertEqual(InitialData({},'.',None).generation,1)
