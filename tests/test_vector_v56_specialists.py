import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import unittest
import numpy as np
import torch
from types import SimpleNamespace
from tests.test_vector_v56_execution import fixture
from research.vectorized_backtest.v56.evaluator import Execution,step
from research.vectorized_backtest.v56.ticker_selection import update_equity,ticker_scores,specialist_pools

class SpecialistTests(unittest.TestCase):
    def test_entry_and_add_over_both_old_limits_execute(self):
        e=Execution(risk_limits_enabled=False).validate()
        for device in ('cpu','cuda') if torch.cuda.is_available() else ('cpu',):
            args,v=fixture(device,1,1);args[8]=args[8].clone();args[8][...,0]=.8
            outputs=[]
            for distance,budget in ((.2,300.),(e.admission_distance,e.admission_budget)):
                p,a=step(*args,risk_protection=True,maximum_stop_distance=distance,maximum_position_risk=budget,bid=v(10),ask=v(10),quote_valid=v(True,torch.bool));outputs.append((p,a))
            self.assertEqual(outputs[0][0][...,0].item(),0);self.assertGreater(outputs[1][0][...,0].item(),0)
            p,a=outputs[1];args[0]=p;args[1]=a;args[6]=v(4,torch.int16);args[8][...,4]=-1;args[9]=torch.tensor(2.,device=device)
            x,y=step(*args,risk_protection=True,maximum_stop_distance=e.admission_distance,maximum_position_risk=e.admission_budget,bid=v(10),ask=v(10),quote_valid=v(True,torch.bool))
            self.assertGreater(x[...,0].item(),p[...,0].item());self.assertEqual(y[...,14:16].sum().item(),0)
            if device=='cuda':
                from research.vectorized_backtest.v56.protected_transition import transition
                z,w=transition(*args,e.admission_distance,e.admission_budget,torch.ones((1,1,1,3),device=device,dtype=torch.float64),0.,v(10),v(10),v(True,torch.bool))
                torch.testing.assert_close(z,x);torch.testing.assert_close(w,y)
    def test_ticker_equity_drawdown_includes_unrealized_losses(self):
        state=torch.zeros((1,1,1,2),dtype=torch.float64);p=torch.zeros((1,1,1,9),dtype=torch.float64);p[...,0]=10;p[...,1]=10
        for mark in (10.,12.,9.,11.):update_equity(state,p,torch.tensor(mark),torch.tensor(True))
        self.assertEqual(state[...,0].item(),20);self.assertEqual(state[...,1].item(),30)
        update_equity(state,p,torch.tensor(1.),torch.tensor(False));self.assertEqual(state[...,1].item(),30)
    def test_local_score_attribution_and_invalid_parent_exclusion(self):
        item=SimpleNamespace(listing_ids=np.array([0,1]),receipt={'listings':[{'listing_id':'a'},{'listing_id':'b'}]})
        m=dict(ticker_pnl=[{'a':10.,'b':-3.}],episode_listings=[[0,0,1]],episode_profits=[[15.,-5.,-3.]],ticker_drawdown=[[7.,4.]])
        rows=ticker_scores(m,item);self.assertEqual(rows,[{'a':-9.,'b':-14.}])
        pools=specialist_pools([dict(ticker_score=[{'a':-20.},{'a':100.},{'a':1000.},{'a':2000.}])],np.array([1,1,0,1],bool),np.array([1,1,1,0],bool),np.arange(4))
        self.assertEqual(pools,{'a':[1,0]})
