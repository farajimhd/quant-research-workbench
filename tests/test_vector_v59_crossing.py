import unittest
from types import SimpleNamespace
import numpy as np
import torch
from research.vectorized_backtest.v59.genome import Network,Neuron,Individual,STAGES,MARKET_INPUTS
from research.vectorized_backtest.v59.templates import Graph,INDEX
from research.vectorized_backtest.v59.network import NetworkPlan
from research.vectorized_backtest.v59.orders import ORDER_WIDTH
from research.vectorized_backtest.v27.genome import Policy
from research.vectorized_backtest.v42.features import CATALOG


class Crossing(unittest.TestCase):
    def run_network(self,device,pending=False):
        c=len(CATALOG);source=INDEX['enriched.return.5s']
        g=Graph();cross=g.finish(g.cross('enriched.return.5s'))
        quiet=Network((Neuron((source,),(1.,),-2.),),threshold=0.).validate()
        nets={s:quiet for s in STAGES};nets['entry']=cross
        if pending:
            nets['exit']=Network((Neuron((source,MARKET_INPUTS+8),(.01,1.),0.),)).validate()
        m=Individual(nets,Policy(),{}).validate();plan=NetworkPlan([m],device)
        values=torch.zeros((5,2,3*c),device=device);known=torch.ones((5,2,c),dtype=torch.bool,device=device)
        values[:,:,source]=torch.tensor([-.2,.2,.4,-.2,.2],device=device)[:,None]
        known[3,1,source]=False
        plan.project=lambda data,index:(values.index_select(0,index.reshape(1))[0],known.index_select(0,index.reshape(1))[0])
        data=SimpleNamespace(resident_features=SimpleNamespace(tensors={'clocks':torch.arange(5,device=device)}),tensors={'membership':torch.ones((5,2),device=device,dtype=torch.bool)})
        p=torch.zeros((1,1,2,9),device=device,dtype=torch.float64);opened=torch.zeros((1,1,2),device=device,dtype=torch.float64)
        plan.pending_orders=torch.zeros((1,1,2,ORDER_WIDTH),device=device,dtype=torch.float64)
        if pending:plan.pending_orders[...,0]=1;plan.pending_orders[...,1]=5;plan.pending_orders[...,3]=10;plan.pending_orders[...,4]=5
        result=[]
        for tick in range(5):result.append(plan.signals(data,torch.tensor(tick,device=device),p,opened,torch.ones((1,1,2),device=device,dtype=torch.float64)).cpu().tolist())
        feedback=plan.feedback.cpu().clone();plan.close();return result,feedback

    def test_crossing_is_an_event_and_requires_both_observations(self):
        rows,_=self.run_network('cpu')
        self.assertEqual([r[0][0][0]&1 for r in rows],[0,1,0,0,1])
        self.assertEqual(rows[4][0][0][1]&1,0)

    @unittest.skipUnless(__import__('os').environ.get('V59_TEST_CUDA')=='1','opt-in GPU qualification')
    def test_crossing_and_pending_state_cpu_cuda_parity(self):
        for pending in (False,True):
            cpu,cf=self.run_network('cpu',pending);gpu,gf=self.run_network('cuda',pending)
            self.assertEqual(cpu,gpu);torch.testing.assert_close(cf,gf,rtol=0,atol=0)
            if pending:
                self.assertTrue(gpu[1][0][0][0]&2)
                self.assertEqual(gf[0,0,3].item(),.5)

    def test_all_templates_remain_feedforward_and_within_budget(self):
        from research.vectorized_backtest.v59.templates import sample_templates
        members,_=sample_templates();self.assertEqual(len(members),3072)
        self.assertTrue(any(n.op=='cross' for m in members for net in m.networks.values() for n in net.nodes))
        for m in members:
            m.validate()
            self.assertTrue(all(s>=0 for net in m.networks.values() for n in net.nodes for s in n.sources))
