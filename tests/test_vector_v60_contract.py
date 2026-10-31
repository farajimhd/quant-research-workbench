import unittest
import numpy as np
import torch
from research.vectorized_backtest.v60.authority import ALLOWED, BLOCKED, feature, dependency_certificate
from research.vectorized_backtest.v60.genome import sample
from research.vectorized_backtest.v60.population_builder import build, canonical_key
from research.vectorized_backtest.v60.orders import transition, ORDER_WIDTH


class ConstructionContract(unittest.TestCase):
    def test_score_keeps_full_drawdown_and_admission_counts(self):
        from research.vectorized_backtest.v60.full_common import aggregate
        from research.vectorized_backtest.v60.robust_score import reward
        panels=[]
        for profit,dd in ((6000.,80.),(-200.,20.),(0.,0.)):
            panels.append(dict(net_pnl=[profit],drawdown=[dd],closed_positions=[int(profit!=0)],winning_positions=[int(profit>0)],
                maximum_position_risk=[0.],maximum_stop_distance=[0.],terminal_valid=[True],
                episode_profits=[[profit]] if profit else [[]],episode_exit_clocks=[[1]] if profit else [[]],
                episode_listings=[[0]] if profit else [[]],admission_entry_signals=[3],admission_entry_requests=[1]))
        result=aggregate(panels)
        expected=sum(.5*reward(p,2000.)+.5*reward(p,5000.)-d for p,d in ((6000.,80.),(-200.,20.),(0.,0.)))
        self.assertEqual(result['score'][0],expected)
        self.assertEqual(result['admission_entry_signals'][0],9)
        self.assertTrue(result['parent_eligible'][0])

    def test_joint_entry_probe_uses_stop_and_limit_boundaries(self):
        from types import SimpleNamespace
        from research.vectorized_backtest.v60.calibration import entry_boundary
        member=SimpleNamespace(policy=SimpleNamespace(swing_left=0,stop_fraction=.03,buy_limit_offset=0.))
        probe=dict(eligible=np.ones(4,bool),quote_valid=np.array([True,False,True,True]),
                   ask=np.array([10.,10.,10.,0.]),bid=np.array([9.9,9.9,10.1,0.]))
        self.assertEqual(entry_boundary(member,probe).tolist(),[True,False,False,False])
        member.policy.buy_limit_offset=-.05
        self.assertFalse(entry_boundary(member,probe).any())

    def test_replay_comparison_preserves_order_content_identity(self):
        from research.vectorized_backtest.v60.profile_global import financial_evidence
        a={'net_pnl':[1.], 'execution_orders':[{'receipt':'a','sha256':'same','orders':2,'candidate_index':0}]}
        b={'net_pnl':[1.], 'execution_orders':[{'receipt':'b','sha256':'same','orders':2,'candidate_index':0}]}
        self.assertEqual(financial_evidence(a),financial_evidence(b))
        b['execution_orders'][0]['sha256']='changed'
        self.assertNotEqual(financial_evidence(a),financial_evidence(b))

    def test_structural_schedule_contains_only_feedforward_edits(self):
        from research.vectorized_backtest.v60.schedule import SETTINGS
        self.assertEqual(SETTINGS['structural_fields'],['input','connect','disconnect','grow','remove','operator'])

    def test_swing_lookup_uses_current_membership(self):
        from types import SimpleNamespace
        from research.vectorized_backtest.v60.swing_projection import prepare
        data=SimpleNamespace(host_tensors={'membership':torch.tensor([[False],[True],[False]])},
            bank={'ids':np.arange(3,dtype=np.int32).reshape(3,1),'values':np.tile(np.arange(3)[:,None]+10.,(1,332))})
        result=prepare(data)
        self.assertEqual(result['v60_entry_swing_ids'][:,0].tolist(),[0,1,0])
        self.assertEqual(result['v60_entry_swing_values'][1,0],11.)

    def test_routing_selects_feasible_later_parent(self):
        from types import SimpleNamespace
        from research.vectorized_backtest.v60.routing_plan import RoutingPlan
        plan=RoutingPlan.__new__(RoutingPlan)
        plan.base=SimpleNamespace(signals=lambda *a:torch.tensor([[[1],[1]]],dtype=torch.int16),
                                  ratios=torch.ones((1,2,1,3),dtype=torch.float64))
        plan.parent_index=torch.tensor([0,0]);plan.branch_index=torch.tensor([[0,1]])
        plan.branch_valid=torch.tensor([[True,True]]);plan.owner=torch.zeros((1,1),dtype=torch.int64)
        plan.device=torch.device('cpu');plan.leaf_columns=torch.tensor([0,1])
        plan.policies=torch.tensor([[.03,.05,.25,.03,0,0,2,1,2,0,0]]*2,dtype=torch.float64)
        plan.ages=torch.zeros((2,5));plan.ratios=torch.empty((1,1,1,3),dtype=torch.float64)
        plan.bind(torch.empty((1,1,1,11),dtype=torch.float64),torch.empty((1,1,1,5)),torch.empty((1,1),dtype=torch.int64))
        data=SimpleNamespace(tensors={'ask':torch.tensor([[8.]]),'quote_valid':torch.tensor([[True]]),
            'membership':torch.tensor([[True]]),'v60_entry_swing_values':torch.tensor([[float('nan')]*2,[9.,7.]]),
            'v60_entry_swing_ids':torch.tensor([[1]])})
        signal=plan.signals(data,torch.tensor(0),torch.zeros((1,1,1,9)),torch.zeros((1,1,1)),torch.tensor([[[8.]]]))
        self.assertEqual(plan.owner.item(),1)
        self.assertEqual(signal.item(),1)

    def test_base_quote_catalog_offsets_are_not_shifted_by_new_indicators(self):
        from research.vectorized_backtest.v60.base_resident_features import ResidentFeatures
        from research.vectorized_backtest.v60.features import ORIGINAL
        obj=ResidentFeatures.__new__(ResidentFeatures)
        obj.device=torch.device('cpu');obj.column_plans={}
        obj.tensors={k:torch.tensor([v],dtype=torch.float64) for k,v in
            dict(feature_row=0,mark=10.,bid=9.9,ask=10.1,quote_us=1000000,quote_seconds=1.,
                 market_clock=1.,log_volume=0.,log_notional=0.,log_trade_count=0.).items()}
        columns=[i for i,f in enumerate(ORIGINAL) if f.name in ('market.bid','market.ask')]
        rows=torch.zeros((1,1),dtype=torch.int64)
        values,known=obj._values(rows,rows,torch.ones((1,1)),columns)
        torch.testing.assert_close(values,torch.tensor([[[9.9,10.1]]]))
        self.assertTrue(known.all())

    def test_vectorized_probe_projection_keeps_each_clock_independent(self):
        from research.vectorized_backtest.v60.feature_policy import transform_projected
        from research.vectorized_backtest.v60.features import CATALOG
        generator=torch.Generator().manual_seed(51)
        values=torch.rand((2,3,7,3*len(CATALOG)),generator=generator)*1.8-.9
        expected=torch.stack([torch.stack([transform_projected(clock) for clock in window]) for window in values])
        torch.testing.assert_close(transform_projected(values),expected,rtol=0,atol=0)

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

    def tick(self, signal=1, bid=10., ask=10., capacity=100., clock=None, terminal=False):
        self.clock = self.clock + 1 if clock is None else clock
        shape=(1,1,1)
        v=lambda x: torch.full(shape, x, dtype=torch.float64)
        self.p,self.o,done,record=transition(self.p,self.o,
            signals=torch.full(shape,signal,dtype=torch.int64),ratios=torch.ones((*shape,3),dtype=torch.float64),
            policy=self.policy,mark=v(bid),previous=v(bid),membership=torch.ones(shape,dtype=torch.bool),
            observed=torch.ones(shape,dtype=torch.bool),swing=v(9.),bid=v(bid),ask=v(ask),
            quote_valid=torch.ones(shape,dtype=torch.bool),capacity=torch.full((*shape,2),capacity,dtype=torch.float64),
            clock=torch.tensor(float(self.clock)),terminal=torch.tensor(terminal),entry_dollars=1000.,add_dollars=200.,
            fee_per_share=.005,minimum_order_fee=1.,cost=0.,minimum_order_dollars=0.)
        return done,record

    def test_flat_exit_head_does_not_veto_entry_or_cancel_unfilled_entry(self):
        self.tick(signal=3,capacity=0.)
        self.assertEqual(self.o[...,0].item(),1.)
        self.tick(signal=2,capacity=10.)
        self.assertEqual(self.p[...,0].item(),10.)
        self.assertEqual(self.o[...,15].item(),0.)
        self.tick(signal=2,capacity=100.)
        self.assertEqual(self.p[...,0].item(),0.)
        self.assertEqual(self.o[...,15].item(),1.)

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

    def test_terminal_controller_finishes_nonmarketable_standing_sell(self):
        self.tick()
        self.policy[...,10]=-.01
        self.tick(signal=2)
        self.assertEqual(self.p[...,0].item(),100.)
        self.tick(signal=0,terminal=True)
        self.assertEqual(self.p[...,0].item(),0.)


if __name__ == '__main__': unittest.main()
