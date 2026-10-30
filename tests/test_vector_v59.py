import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
import torch
from research.vectorized_backtest.v59 import robust_score as score
from research.vectorized_backtest.v59.genome import sample,Individual,Network,Neuron,STAGES
from research.vectorized_backtest.v59.strategy_mix import cross,node_count
from research.vectorized_backtest.v59.lineage import program_key

class V59Tests(unittest.TestCase):
    def test_reward_monotonic_loss_and_targets(self):
        p=np.array([-1e6,-500,0,499.999,500,500.001,1000,5000,1e6])
        v=score.reward(p,500)
        self.assertTrue(np.all(np.diff(v)>0))
        np.testing.assert_equal(v[:5],p[:5])
        self.assertAlmostEqual(v[6],500+50*np.log(2))
        self.assertLess(v[-1],1000)
    def test_ticker_aggregation_precedes_cap(self):
        keys,v=score.ticker_costs([800,-300,400],[1,1,2])
        np.testing.assert_equal(keys,[1,2]);np.testing.assert_equal(v,[500,400])
    def test_objective_retains_all_drawdown_and_open_losses(self):
        from research.vectorized_backtest.v59.full_common import aggregate
        m=dict(net_pnl=[99.],drawdown=[20.],closed_positions=[1],winning_positions=[1],maximum_position_risk=[0.],maximum_stop_distance=[0.],terminal_valid=[False],episode_profits=[[100.]],episode_listings=[[0]],episode_exit_clocks=[[1]],open_episode_realized=[[-1.]],open_episode_listings=[[1]])
        p=aggregate([m]);self.assertEqual(p['score'][0],79.)
        m['net_pnl']=[100.]
        with self.assertRaisesRegex(ValueError,'reconcile'):aggregate([m])
    def test_feature_policy_blocks_all_channels(self):
        from research.vectorized_backtest.v59.feature_policy import BLOCKED,CATALOG
        for channel in range(3):
            with self.assertRaisesRegex(ValueError,'Forbidden'):
                Network((Neuron((next(iter(BLOCKED))+channel*len(CATALOG),),(1.,),0.),)).validate()
        for m in sample(np.random.default_rng(44),5):m.validate()
    def test_strategy_roundtrip_and_budget(self):
        a,b=sample(np.random.default_rng(3),2)
        for mode in ('entry_routing','reciprocal_veto','entry_management_exchange'):
            child,_=cross(a,b,mode)
            self.assertEqual(program_key(child),program_key(Individual.restore(child.payload())))
            self.assertLessEqual(node_count(child),1024)
        routed,_=cross(a,b,'entry_routing')
        with self.assertRaisesRegex(ValueError,'budget'):cross(routed,routed,'entry_routing') if node_count(routed)>512 else cross(cross(routed,routed,'entry_routing')[0],routed,'entry_routing')
    def test_split_fallback_does_not_hide_validation(self):
        from research.vectorized_backtest.v59.campaign_progress import Model
        live=dict(generation=3,aggregates=dict(training=dict(coverage=2,candidates=[3])))
        sealed=dict(generation=2,aggregates=dict(validation=dict(coverage=12,candidates=[2])))
        model=SimpleNamespace(job={'validation_days':list(range(12))},live_snapshot=lambda _:live,global_replays=lambda:None,global_snapshot=lambda:sealed)
        self.assertEqual(Model.aggregate_report(model,'validation')['candidates'],[2])
    def test_sticky_routing_selects_policy_sizes_and_resets(self):
        from research.vectorized_backtest.v59.routing_plan import RoutingPlan
        a,b=sample(np.random.default_rng(9),2)
        # Use small legal graphs to keep the full routing budget deterministic.
        from research.vectorized_backtest.v59.feature_policy import ALLOWED
        net=Network((Neuron((ALLOWED[0],),(1.,),0.),))
        a=replace(a,networks={s:net for s in STAGES});b=replace(b,networks={s:net for s in STAGES})
        child,_=cross(a,b,'entry_routing');plan=RoutingPlan([child],'cpu');plan.prepare(1,2)
        policy=torch.zeros((1,1,2,9),dtype=torch.float64);ages=torch.zeros((1,1,2,5),dtype=torch.float64);columns=torch.zeros((1,2),dtype=torch.int64)
        plan.bind(policy,ages,columns);plan.leaf_columns=torch.tensor([7,8])
        position=torch.zeros((1,1,2,9),dtype=torch.float64);opened=torch.zeros((1,1,2),dtype=torch.float64)
        plan.base.ratios[0,0]=.2;plan.base.ratios[0,1]=.8
        raw=torch.tensor([[[0,1],[1,1]]],dtype=torch.int16)
        with patch.object(plan.base,'signals',return_value=raw):
            plan.signals(None,torch.tensor(0),position,opened,torch.ones((1,1,2)))
            self.assertEqual(plan.owner.tolist(),[[1,0]])
            torch.testing.assert_close(plan.ratios[0,0,:,0],torch.tensor([.8,.2],dtype=torch.float64))
            self.assertEqual(columns.tolist(),[[8,7]])
            position[...,0]=1
            raw[0,0]=2;raw[0,1]=4
            result=plan.signals(None,torch.tensor(1),position,opened,torch.ones((1,1,2)))
            self.assertEqual(result.tolist(),[[[4,2]]])
            self.assertEqual(policy[0,0,0,0],b.policy.stop_fraction)
        plan.reset();self.assertEqual(plan.owner.sum(),0)
        plan.close()

    def test_full_routed_replay_and_position_branch(self):
        from research.vectorized_backtest.v59.evaluator import replay_cohort,Execution
        from research.vectorized_backtest.v59.routing_plan import RoutingPlan
        from research.vectorized_backtest.v59.feature_policy import ALLOWED,CATALOG
        def run(device):
            c=len(CATALOG);f=ALLOWED[0]
            off=Network((Neuron((f,),(1.,),-1.),),size_weight=0.,size_bias=0.)
            on=Network((Neuron((f,),(1.,),1.),),size_weight=0.,size_bias=0.)
            a,b=sample(np.random.default_rng(1),2)
            policy=replace(a.policy,stop_fraction=.05,target_fraction=.9,swing_left=0,swing_right=0)
            a=replace(a,policy=policy,networks={s:off for s in STAGES},minimum_age={})
            b=replace(b,policy=policy,networks={**a.networks,'entry':on},minimum_age={})
            m=cross(a,b,'entry_routing')[0];plan=RoutingPlan([m],device)
            values=torch.zeros((1,3*c),device=device);valid=torch.ones((1,c),device=device,dtype=torch.bool)
            plan.base.project=lambda data,index:(values,valid)
            data=SimpleNamespace(device=torch.device(device),clocks=3,listing_ids=[0],quote_coverage={'maximum_age_seconds':1.},
                tensors={k:torch.full((3,1),v,device=device,dtype=torch.float64 if k in ('mark','bid','ask') else torch.bool) for k,v in dict(mark=10.,bid=9.9,ask=10.1,quote_valid=True,observed=True,membership=True).items()})
            data.tensors['history_ids']=torch.zeros((3,1),device=device,dtype=torch.int64)
            data.resident_features=SimpleNamespace(tensors={'clocks':torch.arange(3,device=device)})
            data.swing_bank=lambda members:(torch.full((1,2),9.7,device=device,dtype=torch.float64),torch.tensor([0,1],device=device))
            result=replay_cohort([data],[m],[plan],trace=True,backend='compile' if device=='cuda' else 'eager',execution=Execution(risk_limits_enabled=False))[0]
            self.assertEqual(result['episode_branch'],[[1]])
            self.assertEqual(result['closed_positions'].tolist(),[1.])
            self.assertAlmostEqual(result['net_pnl'].item(),500/10.1*(9.9-10.1))
            plan.close();return result['net_pnl'].cpu()
        cpu=run('cpu')
        if os.environ.get('V59_TEST_CUDA')=='1':torch.testing.assert_close(run('cuda'),cpu,rtol=0,atol=0)

if __name__=='__main__':unittest.main()
