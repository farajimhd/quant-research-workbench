import unittest
import numpy as np
import torch
from research.vectorized_backtest.v60.authority import ALLOWED, BLOCKED, feature, dependency_certificate
from research.vectorized_backtest.v60.genome import sample
from research.vectorized_backtest.v60.population_builder import build, canonical_key
from research.vectorized_backtest.v60.orders import transition, ORDER_WIDTH


class ConstructionContract(unittest.TestCase):
    def test_restored_prices_and_forbidden_inputs(self):
        self.assertEqual((len(ALLOWED), len(BLOCKED)), (834, 123))
        for name in ('market.bid', 'market.ask', 'market.spread', 'indicator.ema.14', 'indicator.atr.14'):
            self.assertTrue(feature(name).allowed)
        for name in ('time_of_day_sin', 'market.quote_age_seconds', 'v7.below.1.role_support'):
            with self.assertRaises(ValueError): feature(name)
        self.assertEqual(feature('market.bid').clamp(20000), 10000)

    def test_complete_population_replaces_rejections_deterministically(self):
        def reject(rng, slot, attempt): raise ValueError('incompatible operator')
        def fresh(rng, slot, attempt): return sample(rng, 1)[0]
        a, report = build(8, reject, fresh, seed=17, attempts=2)
        b, _ = build(8, reject, fresh, seed=17, attempts=2)
        self.assertEqual([canonical_key(x) for x in a], [canonical_key(x) for x in b])
        self.assertEqual(len(set(map(canonical_key, a))), 8)
        self.assertEqual(report['fallback_fresh'], 8)
        self.assertEqual(report['rejected:incompatible operator'], 16)
        for member in a: dependency_certificate(member)


class MinimalExecution(unittest.TestCase):
    def setUp(self):
        self.p = torch.zeros((1,1,1,9), dtype=torch.float64)
        self.o = torch.zeros((1,1,1,ORDER_WIDTH), dtype=torch.float64)
        self.policy = torch.tensor([.03,.05,.25,.03,0.,0.,20.,1.,0.,0.,0.], dtype=torch.float64).reshape(1,1,1,11)
        self.clock = 0

    def tick(self, signal=1, bid=10., ask=10., capacity=100., clock=None):
        self.clock = self.clock + 1 if clock is None else clock
        shape=(1,1,1)
        v=lambda x: torch.full(shape, x, dtype=torch.float64)
        self.p,self.o,done,record=transition(self.p,self.o,
            signals=torch.full(shape,signal,dtype=torch.int64),ratios=torch.ones((*shape,3),dtype=torch.float64),
            policy=self.policy,mark=v(bid),previous=v(bid),membership=torch.ones(shape,dtype=torch.bool),
            observed=torch.ones(shape,dtype=torch.bool),swing=v(9.),bid=v(bid),ask=v(ask),
            quote_valid=torch.ones(shape,dtype=torch.bool),capacity=torch.full((*shape,2),capacity,dtype=torch.float64),
            clock=torch.tensor(float(self.clock)),terminal=torch.tensor(False),entry_dollars=1000.,add_dollars=200.,
            fee_per_share=.005,minimum_order_fee=1.,cost=0.,minimum_order_dollars=0.)
        return done,record

    def test_candidate_limit_waits_then_fills_at_available_ask(self):
        self.policy[...,9]=-.01
        self.tick(capacity=100.)
        self.assertEqual(self.p[...,0].item(),0.)
        self.assertEqual(self.o[...,16].item(),9.9)
        self.tick(signal=0,bid=9.85,ask=9.9)
        self.assertEqual(self.p[...,0].item(),100.)
        self.assertAlmostEqual(self.p[...,1].item(),9.9)
        self.assertAlmostEqual(self.p[...,2].item(),9.7)

    def test_partial_fill_is_not_amended_by_repeated_add_signal(self):
        self.tick(capacity=30.)
        self.tick(signal=4,capacity=0.)
        self.assertEqual(self.o[...,3].item(),100.)
        self.assertEqual(self.o[...,1].item(),70.)
        self.tick(signal=0,capacity=70.)
        self.assertEqual(self.p[...,0].item(),100.)
        self.assertEqual(self.p[...,7].item(),-1.)

    def test_entry_stop_rule_does_not_add_a_spread_risk_gate(self):
        self.tick(bid=9.,ask=10.)
        self.assertEqual(self.p[...,0].item(),100.)
        self.assertAlmostEqual(self.p[...,2].item(),9.7)

    def test_exit_and_three_second_reentry_boundary(self):
        self.tick(capacity=30.)
        self.tick(signal=2)
        self.assertEqual(self.p[...,0].item(),0.)
        self.tick(clock=4)
        self.assertEqual(self.p[...,0].item(),0.)
        self.tick(clock=5)
        self.assertEqual(self.p[...,0].item(),100.)


if __name__ == '__main__': unittest.main()
