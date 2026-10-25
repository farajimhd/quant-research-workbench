import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
os.environ.setdefault('TRITON_CACHE_DIR','D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v50/qualification/test-cache/triton')
os.environ.setdefault('TORCHINDUCTOR_CACHE_DIR','D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v50/qualification/test-cache/inductor')
import unittest
from types import SimpleNamespace
import numpy as np
import torch
from research.vectorized_backtest.v50.genome import Network,Neuron,Individual,Policy,STAGES
from research.vectorized_backtest.v50.whole_network import cross
from research.vectorized_backtest.v50.network import NetworkPlan
from research.vectorized_backtest.v50.genome import MARKET_INPUTS
from research.vectorized_backtest.v42.features import CATALOG


def parent(column, weight=.2, bias=0., recurrent=False):
    n=Neuron((column,-1) if recurrent else (column,), (1.,.3) if recurrent else (1.,),0.,op='identity')
    net=Network((n,),.1,0,weight,bias)
    return Individual({s:net for s in STAGES},Policy(),{s:0 for s in STAGES}).validate()


def signals(members, device, ticks=3, known=True):
    plan=NetworkPlan(members,device);u=4;b=len(members)
    values=torch.zeros((u,MARKET_INPUTS),device=device)
    values[:,0]=torch.tensor([.5,.5,-.5,-.5],device=device)
    values[:,1]=torch.tensor([.5,-.5,.5,-.5],device=device)
    valid=torch.full((u,len(CATALOG)),known,device=device,dtype=torch.bool)
    plan._project=lambda *args:(values,valid)
    data=SimpleNamespace(resident_features=SimpleNamespace(tensors={'clocks':torch.arange(100,110,device=device)}))
    p=torch.zeros((1,b,u,9),device=device,dtype=torch.float64)
    opened=torch.zeros((1,b,u),device=device,dtype=torch.float64)
    previous=torch.ones((1,1,u),device=device,dtype=torch.float64)
    result=[]
    for clock in range(ticks):
        out=plan.signals(data,torch.tensor(clock,device=device),p,opened,previous)
        result.append((out.cpu().clone(),plan.ratios.cpu().clone()))
    plan.close();return result


class WholeNetworkTests(unittest.TestCase):
    def test_original_numerical_path_and_identity_cache(self):
        from research.vectorized_backtest.v50.genome import sample
        from research.vectorized_backtest.v50.lineage import program_key
        from research.vectorized_backtest.v50.validation_cache import validation_scope
        original=sample(np.random.default_rng(21),8)
        members=original+[cross(parent(0),parent(1),'union')[0]]
        tokens=[program_key(m) for m in members]
        with validation_scope():self.assertEqual(tokens,[program_key(m) for m in members])
        for (cs,cr),(gs,gr) in zip(signals(members,'cpu'),signals(members,'cuda')):
            self.assertTrue(torch.equal(cs,gs));torch.testing.assert_close(cr,gr,rtol=2e-5,atol=2e-6)

    def test_oversized_composition_rejected(self):
        from research.vectorized_backtest.v50.genome import INPUTS
        from dataclasses import replace
        a=parent(0)
        nodes=tuple(Neuron((0 if i==0 else INPUTS+i-1,),(1.,),0.,op='identity') for i in range(96))
        big=Individual({s:Network(nodes) for s in STAGES},a.policy,a.minimum_age).validate()
        with self.assertRaisesRegex(ValueError,'size|budget'):cross(big,big,'union')


    def test_boolean_truth_table_and_max_sizing(self):
        a=parent(0,.4,-.1);b=parent(1,.8,.2)
        parents=signals([a,b],'cpu',1)[0]
        for mode,truth in [('union',[1,1,1,0]),('intersection',[1,0,0,0]),('xor',[0,1,1,0])]:
            child,_=cross(a,b,mode);actual,ratios=signals([child],'cpu',1)[0]
            self.assertEqual((actual[0,0]&1).tolist(),truth)
            expected=torch.tensor([max(parents[1][0,0,0,0],parents[1][0,1,0,0]),parents[1][0,0,1,0],parents[1][0,1,2,0]],dtype=torch.float64)
            torch.testing.assert_close(ratios[0,0,:3,0],expected,rtol=1e-6,atol=1e-7)

    def test_independent_recurrence_and_cpu_cuda_parity(self):
        a=parent(0,recurrent=True);b=parent(1,recurrent=True)
        members=[a,b]+[cross(a,b,m)[0] for m in ('union','intersection','xor')]
        cpu=signals(members,'cpu')
        if not torch.cuda.is_available():self.skipTest('CUDA unavailable')
        gpu=signals(members,'cuda')
        for (cs,cr),(gs,gr) in zip(cpu,gpu):
            self.assertTrue(torch.equal(cs,gs));torch.testing.assert_close(cr,gr,rtol=1e-6,atol=1e-7)
            a0=cs[0,0];b0=cs[0,1]
            self.assertTrue(torch.equal(cs[0,2],a0|b0))
            self.assertTrue(torch.equal(cs[0,3],a0&b0))
            self.assertTrue(torch.equal(cs[0,4],a0^b0))

    def test_missing_market_data_cannot_fire(self):
        child,_=cross(parent(0),parent(1),'union')
        for device in ('cpu','cuda') if torch.cuda.is_available() else ('cpu',):
            self.assertEqual(signals([child],device,1,False)[0][0].count_nonzero().item(),0)

    def test_payload_roundtrip_and_nested_composition(self):
        child,_=cross(parent(0),parent(1),'union')
        restored=Individual.restore(child.payload())
        self.assertEqual(restored.payload(),child.payload())
        nested,_=cross(child,parent(0),'intersection')
        self.assertTrue(torch.equal(signals([nested],'cpu',1)[0][0], signals([nested],'cuda',1)[0][0]))

    def test_parent_age_gate_preserved(self):
        a=parent(0);a=Individual(a.networks,a.policy,{s:2 for s in STAGES})
        b=parent(1);child,_=cross(a,b,'union')
        for device in ('cpu','cuda') if torch.cuda.is_available() else ('cpu',):
            rows=signals([child],device,3)
            self.assertEqual(int(rows[0][0][0,0,1]),0)
            self.assertEqual(int(rows[2][0][0,0,1]),31)


if __name__=='__main__':unittest.main()
