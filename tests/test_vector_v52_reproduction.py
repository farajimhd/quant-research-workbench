import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import importlib,json,unittest
import numpy as np
from tests.test_vector_v52_pilot import members,metrics
from research.vectorized_backtest.v52.identity import digest

class ReproductionTests(unittest.TestCase):
    def reproduce(self,version,values):
        lineage=importlib.import_module(f'research.vectorized_backtest.{version}.lineage')
        genome=importlib.import_module(f'research.vectorized_backtest.{version}.genome')
        runner=importlib.import_module(f'research.vectorized_backtest.{version}.local_runner')
        pop=[genome.Individual.restore(m.payload()) for m in members(len(values))]
        pedigree=runner.CompactPedigree(3)
        ids=pedigree.birth(pop,1,[(-1,-1)]*len(pop),['fresh']*len(pop))
        values=np.asarray(values,float);valid=np.ones(len(pop),bool);rng=np.random.default_rng(2021)
        result,newids,telemetry=lineage.next_population(rng,pop,ids,pedigree,[],2,session_values=values[None],session_eligible=valid[None],global_values=values,global_eligible=valid,target_size=40,ordered=[metrics(10,len(pop))])
        return dict(population=[m.payload() for m in result],ids=newids.tolist(),rng=rng.bit_generator.state),pedigree,telemetry

    def test_v49_exact_reproduction_parity_and_positive_parents(self):
        values=[100-i for i in range(30)]+[0]*5+[-1]*5
        old,_,_=self.reproduce('v49',values)
        new,pedigree,telemetry=self.reproduce('v52',values)
        self.assertEqual(digest(json.loads(json.dumps(old).replace('v49-whole-v1','v52-whole-v1'))),digest(new))
        self.assertGreater(telemetry['crossovers'],0)
        for row in pedigree.records.values():
            for parent in row['parents']:
                if parent>=0:self.assertGreater(values[parent],0)

    def test_no_positive_parents_uses_fresh_fallback_without_self_mating(self):
        state,pedigree,telemetry=self.reproduce('v52',[0]*20+[-1]*20)
        self.assertEqual(telemetry['crossovers'],0)
        self.assertEqual(telemetry['fresh_candidates'],40)
        self.assertEqual(len(state['population']),40)
        self.assertTrue(all(pedigree.records[i]['parents']==[-1,-1] for i in state['ids']))

    def test_proposal_seed_changes_random_programs(self):
        from research.vectorized_backtest.v52.genome import sample
        one=[m.payload() for m in sample(np.random.default_rng(2020),6)]
        two=[m.payload() for m in sample(np.random.default_rng(2021),6)]
        self.assertNotEqual(digest(one),digest(two))
        self.assertEqual(digest(two),digest([m.payload() for m in sample(np.random.default_rng(2021),6)]))
