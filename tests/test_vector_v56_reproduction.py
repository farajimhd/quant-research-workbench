import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import json
import unittest
from dataclasses import replace
from unittest.mock import patch
import numpy as np
from tests.test_vector_v53_pilot import metrics as base_metrics

def metrics(value,n):
    m=base_metrics(value,n);m["ticker_score"]=[{"a":float(value)} for _ in range(n)];return m
def members(n):
    from research.vectorized_backtest.v56.genome import sample
    return sample(np.random.default_rng(2021),n)
from research.vectorized_backtest.v56.genome import sample
from research.vectorized_backtest.v56.lineage import next_population
from research.vectorized_backtest.v56.local_runner import CompactPedigree
from research.vectorized_backtest.v56.identity import digest


class ReproductionTests(unittest.TestCase):
    def reproduce(self, scores, feasible=True, active=True, profit=False):
        pop = members(len(scores))
        pedigree = CompactPedigree(3)
        ids = pedigree.birth(pop, 1, [(-1, -1)] * len(pop), ['fresh'] * len(pop))
        values = np.asarray(scores, float)
        rng = np.random.default_rng(2021)
        observed=metrics(10,len(pop))
        if profit:observed['net_pnl']=list(map(float,range(len(pop))))
        if not active:observed['closed_positions']=[0]*len(pop)
        with patch('research.vectorized_backtest.v56.initial_population.immigrant',
                   side_effect=lambda rng, probe: sample(rng, 1)[0]), \
             patch('research.vectorized_backtest.v56.whole_network.fits', return_value=feasible):
            from research.vectorized_backtest.v56.lineage import Selection
            result, newids, telemetry = next_population(rng, pop, ids, pedigree, [], 2,Selection(profit_fresh_fraction=.25 if profit else 0.),
                session_values=values[None], session_eligible=np.ones((1, len(pop)), bool),
                global_values=values, global_eligible=np.ones(len(pop), bool),
                target_size=40, ordered=[observed], probe={})
        return result, newids, telemetry, pedigree, rng.bit_generator.state

    def test_profit_leaders_replace_only_fresh_slots(self):
        result,ids,t,ped,_=self.reproduce(list(range(100,60,-1)),profit=True)
        self.assertEqual(len(result),40)
        self.assertEqual(ids[:6].tolist(),[0,1,2,3,39,38])
        self.assertEqual((t['elites'],t['profit_elites'],t['baseline_fresh'],t['requested_crossovers']),(4,2,6,28))
        self.assertEqual(t['profit_elite_ids'],[39,38])
        from research.vectorized_backtest.v56.allocation import budgets
        b=budgets(1024);self.assertEqual((b['elites'],int(b['fresh']*.25),b['fresh']-int(b['fresh']*.25),b['crossovers']),(102,51,154,717))

    def test_repeatability_all_four_modes_and_unmutated_crossovers(self):
        a = self.reproduce(list(range(100, 60, -1)))
        b = self.reproduce(list(range(100, 60, -1)))
        self.assertEqual(digest(([m.payload() for m in a[0]], a[1].tolist(), a[4])),
                         digest(([m.payload() for m in b[0]], b[1].tolist(), b[4])))
        telemetry, pedigree = a[2:4]
        self.assertEqual(set(telemetry['whole_network']['requested_modes']),
                         {'union', 'intersection', 'xor', 'xnor'})
        self.assertGreater(telemetry['crossovers'], 0)
        self.assertGreater(telemetry['ticker_parent_selections'],0)
        self.assertGreater(telemetry['global_parent_selections'],0)
        for record in pedigree.records.values():
            if record['generation'] == 2 and record['operation'].startswith('crossover'):
                self.assertEqual(record['operation'], 'crossover')
                if record.get('whole_network'):
                    self.assertEqual(len(record['crossover']['actions']), 1)

    def test_infeasible_whole_pairs_use_parent_mutation_not_extra_randoms(self):
        _, _, telemetry, pedigree, _ = self.reproduce(list(range(100, 60, -1)), False)
        self.assertGreater(telemetry['whole_network']['budget_filtered_mate_options'], 0)
        self.assertEqual(telemetry['whole_network']['accepted'], 0)
        self.assertGreater(telemetry['parent_mutations'], 0)
        self.assertEqual(telemetry['fresh_candidates'], telemetry['baseline_fresh'])
        for record in pedigree.records.values():
            if record['operation'] == 'parent_mutation':
                self.assertGreaterEqual(record['parents'][0], 0)
                self.assertEqual(record['parents'][1], -1)

    def test_no_active_parent_reports_fresh_fallback(self):
        result, _, telemetry, _, _ = self.reproduce([-1.] * 40,active=False)
        self.assertEqual(len(result), 40)
        self.assertEqual(telemetry['crossovers'], 0)
        self.assertEqual(telemetry['parent_mutations'], 0)
        self.assertEqual(telemetry['fresh_candidates'], 40)

    def test_negative_fitness_active_candidates_can_be_elites_and_parents(self):
        _, ids, telemetry, pedigree, _ = self.reproduce([-float(i+1) for i in range(40)])
        self.assertEqual(telemetry['active_parent_candidates'],40)
        self.assertEqual(ids[:4].tolist(),[0,1,2,3])
        self.assertEqual(telemetry['fresh_candidates'],telemetry['baseline_fresh'])
        self.assertGreater(telemetry['crossovers']+telemetry['parent_mutations'],0)
        self.assertTrue(any(r['parents'][0]>=0 for r in pedigree.records.values() if r['generation']==2))

    def test_inactive_candidates_cannot_reproduce_even_with_positive_supplied_score(self):
        _, _, telemetry, _, _ = self.reproduce([100.] * 40, active=False)
        self.assertEqual(telemetry['active_parent_candidates'],0)
        self.assertEqual(telemetry['elites'],0)
        self.assertEqual(telemetry['crossovers'],0)

    def test_audit_counts_only_new_children_and_requires_profit_and_eligibility(self):
        from research.vectorized_backtest.v56.offspring_audit import summarize
        records = [dict(generation=g, operation='crossover', parent_objectives=[-2., -1.])
                   for g in (1, 2, 2, 2)]
        panel = dict(score=np.array([10., 0., 2., 3.]), net_pnl=np.array([10., 0., 2., 3.]),
                     eligible=np.array([True, True, True, False]))
        result = summarize(records, panel, 2)['groups']['mask']
        self.assertEqual(result['evaluated'], 3)
        self.assertEqual(result['beat_best_parent_eligible'], 2)
        self.assertEqual(result['beat_best_parent_profitable_eligible'], 1)


if __name__ == '__main__':
    unittest.main()
