import unittest
from src.trading_runtime.normalized_level_book import calibration, transform, merge_levels
from src.trading_runtime.structure_level_contract import strategy_snapshot
from tests.test_point_structure_strategy import row, NOW


def level(identity, lower, upper, price, score, side=1):
    return dict(row(price,side,score), unified_level_id=identity, lower=lower, upper=upper, lifecycle='active')


class NormalizedBookTests(unittest.TestCase):
    def test_transitive_merge_averages_original_members_and_keeps_roles(self):
        rows=[level('a',1,2,1.5,2),level('b',2,3,2.5,4),level('c',3,4,3.5,9),level('r',1,4,2,6,-1)]
        merged=merge_levels(rows)
        support=next(r for r in merged if r['side']==1)
        self.assertEqual(len(merged),2)
        self.assertEqual(support['price'],2.5)
        self.assertEqual(support['prominence'],5)
        self.assertEqual(support['member_count'],3)
        self.assertEqual(merged,merge_levels(list(reversed(rows))))

    def test_fixed_range_and_bounds_do_not_recalibrate_on_future_population(self):
        seed=[level('a',1,2,1.5,2),level('b',3,4,3.5,6),level('outside',30,31,30.5,999)]
        basis=calibration(seed,10)
        self.assertEqual((basis['p_min'],basis['p_max']),(2,6))
        current=seed+[level('new',5,6,5.5,10)]
        result=transform(current,basis)['unified_levels']
        self.assertEqual([r['p_norm'] for r in result],[0,1,1])
        self.assertEqual(basis['p_max'],6)

    def test_equal_scores_neutral_and_empty_seed_unavailable(self):
        seed=[level('a',1,2,1.5,2)]
        self.assertEqual(transform(seed,calibration(seed,10))['unified_levels'][0]['p_norm'],.5)
        self.assertIsNone(transform(seed,calibration([],10))['unified_levels'][0]['p_norm'])

    def test_strategy_uses_normalized_threshold_without_raw_four_gate(self):
        seed=[level('a',1,2,1.5,1),level('b',3,4,3.5,2),level('c',5,6,5.5,3)]
        snapshot=transform(seed,calibration(seed,10))
        self.assertEqual([r['p_norm'] for r in strategy_snapshot(snapshot,NOW,.5)['unified_levels']],[.5,1])
        self.assertEqual(len(strategy_snapshot(snapshot,NOW,.75)['unified_levels']),1)
        self.assertEqual(len(strategy_snapshot(snapshot,NOW,0)['unified_levels']),3)

    def test_actual_strategy_stop_and_target_use_pnorm_and_role(self):
        from src.trading_runtime import strategy_engine as S
        from tests.test_long_momentum_strategy import confirmed_observation
        seed=[level('s1',96.9,97.1,97,3),level('s2',97.9,98.1,98,3),
              level('r1',101.9,102.1,102,3,-1),level('r2',102.9,103.1,103,3,-1),level('r3',103.9,104.1,104,3,-1),
              level('low',95.9,96.1,96,1)]
        snapshot=strategy_snapshot(transform(seed,calibration(seed,100)),NOW,.5)
        rows=snapshot['unified_levels']
        obs=confirmed_observation(price=100,structural_support_levels=tuple(r for r in rows if r['side']==1),
            structural_resistance_levels=tuple(r for r in rows if r['side']==-1))
        p=S.resolve_long_momentum_parameters(revision=47)
        self.assertEqual(S._initial_stop(obs,p,100,side='long'),97)
        self.assertEqual(S._structural_profit_targets(obs,p,stop=97,side='long',luld_target=None),[104])
