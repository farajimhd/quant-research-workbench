import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
os.environ.setdefault('TRITON_CACHE_DIR','D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v54/qualification/test-cache/triton')
os.environ.setdefault('TORCHINDUCTOR_CACHE_DIR','D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v54/qualification/test-cache/inductor')
import unittest
import numpy as np
import torch
from research.vectorized_backtest.v54.data import quote_arrays
from research.vectorized_backtest.v54.evaluator import step
from research.vectorized_backtest.v42.evaluator import step as close_step


def fixture(device='cpu', batch=2, width=3):
    p = torch.zeros((1, batch, width, 9), dtype=torch.float64, device=device)
    a = torch.zeros((1, batch, 16), dtype=torch.float64, device=device); a[..., 8] = float('inf')
    value = lambda x, dtype=torch.float64: torch.full((1, batch, width), x, dtype=dtype, device=device)
    policy = torch.tensor([.03, .05, .25, .03, .01, .02, 2, 1, 0], dtype=torch.float64, device=device)[None,None,None].expand(1,batch,width,9)
    args = [p, a, value(10), value(10), value(True,torch.bool), value(True,torch.bool),
            value(1,torch.int16), value(9.7), policy, torch.tensor(1.,device=device),value(False,torch.bool),
            1000.,200.,0.,.005,1.]
    return args, value


class QuoteExecutionTests(unittest.TestCase):
    def test_quote_age_and_invalid_prices_fail_closed(self):
        m=dict(bid=np.array([9.,9.,9.,9.,11.,np.nan]),ask=np.array([10.,10.,10.,10.,10.,10.]),
               quote_us=np.array([1000000,999999,2000001,0,1000000,1000000]))
        r=quote_arrays(m,np.array([[0,1,2,3,4,5,-1]]),np.array([2]))
        self.assertEqual(r['quote_valid'].tolist(),[[True,False,False,False,False,False,False]])
        self.assertTrue((r['bid'][~r['quote_valid']]==0).all())

    def test_ask_entry_bid_exit_and_commissions(self):
        args,v=fixture();p,a=step(*args,risk_protection=True,bid=v(9.9),ask=v(10.1),quote_valid=v(True,torch.bool))
        q=1000/10.1
        torch.testing.assert_close(p[...,0],v(q));torch.testing.assert_close(p[...,1],v(10.1))
        torch.testing.assert_close(p[...,3],v(10.1*1.05))
        args[:2]=[p,a];args[6]=v(2,torch.int16)
        p,a=step(*args,risk_protection=True,bid=v(9.9),ask=v(10.1),quote_valid=v(True,torch.bool))
        torch.testing.assert_close(p[...,0],v(0.));torch.testing.assert_close(p[...,7],v(q*(9.9-10.1)-2))

    def test_bad_quote_cannot_enter_or_force_terminal_close(self):
        args,v=fixture();p,a=step(*args,risk_protection=True,bid=v(0.),ask=v(0.),quote_valid=v(False,torch.bool))
        self.assertEqual(float(p[...,0].sum()),0.)
        args[0][...,0]=10;args[0][...,1]=10;args[0][...,2]=9;args[10]=v(True,torch.bool)
        p,a=step(*args,risk_protection=True,bid=v(0.),ask=v(0.),quote_valid=v(False,torch.bool))
        torch.testing.assert_close(p[...,0],v(10.))

    def test_equal_quotes_reproduce_close_accounting_exactly(self):
        args,v=fixture();x,y=close_step(*args,risk_protection=True)
        p,a=step(*args,risk_protection=True,bid=v(10),ask=v(10),quote_valid=v(True,torch.bool))
        self.assertTrue(torch.equal(x,p));self.assertTrue(torch.equal(y,a))

    def test_ask_used_for_prospective_risk(self):
        args,v=fixture();args[8]=args[8].clone();args[8][...,8]=1;args[7]=v(8.1)
        # Mark-stop is safe, but buying at the ask violates the 20% stop envelope.
        p,a=step(*args,risk_protection=True,bid=v(9.9),ask=v(11.),quote_valid=v(True,torch.bool))
        self.assertEqual(float(p[...,0].sum()),0.);self.assertGreater(float(a[...,14].sum()),0.)

    def test_real_replay_wrapper_entry_exit_and_feedback(self):
        from types import SimpleNamespace
        from research.vectorized_backtest.v54.evaluator import replay_cohort
        class Plan:
            total_nodes=1
            ratios=torch.ones((1,1,1,4),dtype=torch.float64)
            feedback=torch.zeros((1,1,11),dtype=torch.float32)
            def prepare(self,*args):pass
            def reset(self):self.feedback.zero_()
            def signals(self,data,index,position,opened,previous,out):out.fill_(1 if int(index)==0 else 2)
        data=SimpleNamespace(device=torch.device('cpu'),clocks=3,listing_ids=[0],quote_coverage={'maximum_age_seconds':1.},
            tensors=dict(mark=torch.full((3,1),10.,dtype=torch.float64),
                         bid=torch.full((3,1),9.9,dtype=torch.float64),ask=torch.full((3,1),10.1,dtype=torch.float64),
                         quote_valid=torch.ones((3,1),dtype=torch.bool),observed=torch.ones((3,1),dtype=torch.bool),
                         membership=torch.ones((3,1),dtype=torch.bool),history_ids=torch.zeros((3,1),dtype=torch.int64)))
        data.swing_bank=lambda members:(torch.full((1,1),9.7,dtype=torch.float64),torch.tensor([0]))
        policy=SimpleNamespace(**dict(stop_fraction=.03,target_fraction=.05,reduce_fraction=.25,trail_fraction=.03,
            add_minimum_profit=.01,reduce_minimum_profit=.02,maximum_adds=2,cooldown=1,swing_left=0))
        member=SimpleNamespace(policy=policy,minimum_age={})
        result=replay_cohort([data],[member],[Plan()],trace=True)[0]
        self.assertEqual(result['closed_positions'].tolist(),[1.])
        self.assertAlmostEqual(result['net_pnl'].item(),1000/10.1*(9.9-10.1))
        self.assertTrue(result['terminal_valid'].item())

        # Missing terminal quotes retain entry commission as open cashflow.
        from research.vectorized_backtest.v54.evaluator import Execution
        from research.vectorized_backtest.v54.financial import ticker_totals
        from research.vectorized_backtest.v54.objectives import local_panel
        data.tensors['quote_valid'][-1]=False
        result=replay_cohort([data],[member],[Plan()],trace=True,
                             execution=Execution(fee_per_share=.005,minimum_order_fee=1.))[0]
        result={k:v.tolist() if isinstance(v,torch.Tensor) else v for k,v in result.items()}
        self.assertEqual(result['closed_positions'],[0.])
        self.assertEqual(result['open_episode_realized'],[[-1.]])
        self.assertEqual(result['terminal_valid'],[False])
        data.receipt={'listings':[{'listing_id':'a'}]}
        result['ticker_pnl']=ticker_totals(result,data)
        panel=local_panel([result],case_maps=[dict(day='d',listing_ids=['a'],security_ids=['a'])])
        self.assertEqual(panel['pnl'].tolist(),[[-1.]])
        self.assertEqual(panel['eligible'].tolist(),[[False]])

    @unittest.skipUnless(torch.cuda.is_available(),'CUDA parity requires the laptop GPU')
    def test_fused_gpu_matches_cpu_for_mixed_actions(self):
        import os
        from research.vectorized_backtest.v54.protected_transition import transition
        args,v=fixture('cuda',batch=8,width=19)
        args[0][...,0]=10;args[0][...,1]=9.5;args[0][...,2]=8.9
        args[0][...,3]=11;args[0][...,4]=10;args[0][...,6]=0
        args[0][:,:,::3]=0
        signals=torch.tensor([1,2,4,8,16,0,31,1,2,4,8,0,16,1,2,4,8,0,31],device='cuda',dtype=torch.int16)[None,None].expand(1,8,19)
        args[6]=signals
        bid=v(9.9);ask=v(10.1);valid=v(True,torch.bool);valid[:,:,::7]=False
        ratios=torch.ones((1,8,19,4),dtype=torch.float64,device='cuda')
        expected=step(*[x.cpu() if isinstance(x,torch.Tensor) else x for x in args],risk_protection=True,
                      ratios=ratios.cpu(),bid=bid.cpu(),ask=ask.cpu(),quote_valid=valid.cpu())
        outputs=[]
        for masked in ('0','1'):
            os.environ['V54_MASK_QUOTES']=masked
            actual=transition(*args,.20,300.,ratios,0.,bid,ask,valid)
            for x,y in zip(actual,expected):torch.testing.assert_close(x.cpu(),y,rtol=1e-12,atol=1e-9)
            outputs.append(actual)
        for x,y in zip(*outputs):self.assertTrue(torch.equal(x,y))


if __name__=='__main__':unittest.main()
