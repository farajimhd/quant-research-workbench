import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
os.environ.setdefault('TRITON_CACHE_DIR','D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v54/qualification/triton')
os.environ.setdefault('TORCHINDUCTOR_CACHE_DIR','D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v54/qualification/inductor')
import unittest
from dataclasses import replace
from types import SimpleNamespace
import numpy as np
import torch
from research.vectorized_backtest.v54.genome import sample,Individual,INPUTS,MARKET_INPUTS,HIDDEN,CATALOG,Head,Branch
from research.vectorized_backtest.v54.network import NetworkPlan,state_values
from research.vectorized_backtest.v54.composition import cross,mutation
from research.vectorized_backtest.v54.whole_network import cross as logical
from research.vectorized_backtest.v54.evaluator import step,state_signals
from tests.test_vector_v53_execution import fixture

class SharedTests(unittest.TestCase):
    def members(self,n=3):return sample(np.random.default_rng(2021),n)
    def setup_plan(self,device='cpu',members=None,u=19):
        members=self.members() if members is None else members;p=NetworkPlan(members,device);p.prepare(len(members),u)
        g=torch.Generator().manual_seed(17)
        v=torch.rand((u,MARKET_INPUTS),generator=g).to(device)*2-1;k=torch.ones((u,len(CATALOG)),device=device,dtype=torch.bool)
        p.project=lambda *args:(v,k)
        data=SimpleNamespace(resident_features=SimpleNamespace(tensors={'clocks':torch.arange(10,device=device)*60}))
        position=torch.zeros((1,len(members),u,9),device=device,dtype=torch.float64);opened=torch.zeros((1,len(members),u),device=device,dtype=torch.float64);previous=torch.full((1,1,u),10.,device=device,dtype=torch.float64)
        return p,data,position,opened,previous
    def test_roundtrip_and_single_core(self):
        for m in self.members():
            self.assertEqual(m.payload(),Individual.restore(m.payload()).payload());self.assertEqual(set(m.networks),{'shared'});self.assertEqual(len(m.networks['shared'].nodes),64);self.assertEqual(len(m.networks['shared'].heads),6)
        with self.assertRaises(ValueError):Individual.restore({'version':'v42-recurrent-v1'})
    def test_calibration_does_not_tune_heads_without_known_market_inputs(self):
        from research.vectorized_backtest.v54.initial_population import calibrate
        m=self.members(1)[0];diag={'unresolved_heads':0}
        probe=dict(inputs=np.zeros((1,2,3,INPUTS),np.float32),known=np.zeros((1,2,3,len(CATALOG)),bool),eligible=np.ones((1,2,3),bool),held_states=np.zeros((1,2,3,INPUTS-MARKET_INPUTS),np.float32))
        result=calibrate(np.random.default_rng(1),m,probe,diag)
        self.assertEqual(m.payload(),result.payload());self.assertEqual(diag['unresolved_heads'],5)
    def test_packed_subset_keeps_exact_order_and_parameters(self):
        from research.vectorized_backtest.v54.network_pack import pack_networks,select_packed
        ms=self.members();a=select_packed(pack_networks(ms),[2,0,2]);b=pack_networks([ms[2],ms[0],ms[2]])
        for x,y in zip(a['values'],b['values']):np.testing.assert_array_equal(x,y)
    def test_crossover_has_only_parent_genes_and_all_six_heads(self):
        a,b=self.members(2);c,_=cross(np.random.default_rng(1),a,b)
        for i,n in enumerate(c.networks['shared'].nodes):self.assertIn(n,(a.networks['shared'].nodes[i],b.networks['shared'].nodes[i]))
        for i,h in enumerate(c.networks['shared'].heads):self.assertIn(h,(a.networks['shared'].heads[i],b.networks['shared'].heads[i]))
        self.assertNotEqual(c.payload(),a.payload());self.assertNotEqual(c.payload(),b.payload())
    def test_logical_modes_share_one_core_and_reject_nested(self):
        a,b=self.members(2)
        for mode in ('union','intersection','xor','xnor'):
            c,meta=logical(a,b,mode,('exit',));self.assertIs(c.networks['shared'].nodes,a.networks['shared'].nodes);self.assertFalse(meta['parent_behavior_preserved'])
            with self.assertRaises(ValueError):logical(c,b,mode,('exit',))
        with self.assertRaises(ValueError):logical(a,b,'xor',('stop',))
    def test_mutation_is_bounded_and_deterministic(self):
        a=self.members(1)[0];x=mutation(np.random.default_rng(4),a,5)[0];y=mutation(np.random.default_rng(4),a,5)[0]
        self.assertEqual(x.payload(),y.payload());self.assertNotEqual(a.payload(),x.payload());x.validate()
    def test_recurrence_reset_and_restore(self):
        p,d,pos,opened,prev=self.setup_plan();zero=torch.tensor(0);one=torch.tensor(1)
        first=p.signals(d,zero,pos,opened,prev).clone();saved=p.export_state();p.signals(d,one,pos,opened,prev);second=p.export_state()
        self.assertFalse(torch.equal(saved['memory'],second['memory']));p.restore_state(saved);p.signals(d,one,pos,opened,prev)
        for k,v in second.items():self.assertTrue(torch.equal(v,p.export_state()[k]))
        p.reset();self.assertTrue(torch.equal(first,p.signals(d,zero,pos,opened,prev)))
    def test_missing_features_cannot_trigger_xnor_or_stop(self):
        a,b=self.members(2);m=logical(a,b,'xnor',('entry',))[0];p,d,pos,opened,prev=self.setup_plan(members=[m])
        p.project=lambda *_:(torch.zeros((19,MARKET_INPUTS)),torch.zeros((19,len(CATALOG)),dtype=torch.bool))
        self.assertFalse(bool(p.signals(d,torch.tensor(0),pos,opened,prev).any()))
    def test_stop_bit_survives_age_filters(self):
        x=torch.tensor([[[63]]],dtype=torch.int16);age=torch.zeros((1,1,1));limits=torch.ones((1,1,1,5));held=torch.ones_like(age,dtype=torch.bool)
        self.assertEqual(int(state_signals(x,held,age,limits,False)),32)
    def test_stop_controls_entry_and_never_loosens(self):
        args,v=fixture();r=torch.ones((1,2,3,4),dtype=torch.float64);r[...,3]=.02;args[6]=v(33,torch.int16)
        p,a=step(*args,risk_protection=True,ratios=r,bid=v(9.9),ask=v(10),quote_valid=v(True,torch.bool))
        torch.testing.assert_close(p[...,2],v(9.8));args[:2]=[p,a];args[6]=v(32,torch.int16);r[...,3]=.10
        p,a=step(*args,risk_protection=True,ratios=r,bid=v(9.9),ask=v(10),quote_valid=v(True,torch.bool));torch.testing.assert_close(p[...,2],v(9.8))
        args[:2]=[p,a];r[...,3]=.01
        p,a=step(*args,risk_protection=True,ratios=r,bid=v(9.9),ask=v(10),quote_valid=v(True,torch.bool));torch.testing.assert_close(p[...,2],v(9.9))
        self.assertLessEqual(float(a[...,12].max()),300);self.assertLessEqual(float(a[...,13].max()),.2)
    def test_invalid_stop_falls_back_and_invalid_quotes_do_not_tighten(self):
        args,v=fixture();r=torch.ones((1,2,3,4),dtype=torch.float64);r[...,3]=float('nan');args[6]=v(33,torch.int16)
        p,a=step(*args,risk_protection=True,ratios=r,bid=v(9.9),ask=v(10),quote_valid=v(True,torch.bool));torch.testing.assert_close(p[...,2],v(9.7))
        args[:2]=[p,a];r[...,3]=.001;args[6]=v(32,torch.int16)
        p,a=step(*args,risk_protection=True,ratios=r,bid=v(0),ask=v(0),quote_valid=v(False,torch.bool));torch.testing.assert_close(p[...,2],v(9.7))
    @unittest.skipUnless(torch.cuda.is_available(),'CUDA required')
    def test_cuda_recurrent_heads_match_oracle_and_graph(self):
        ms=self.members();ms[0]=logical(ms[0],ms[1],'xor',('entry',))[0];ms[1]=logical(ms[1],ms[2],'xnor',('exit',))[0]
        cpu=self.setup_plan(members=ms);gpu=self.setup_plan('cuda',ms)
        for tick in range(4):
            x=cpu[0].signals(cpu[1],torch.tensor(tick),*cpu[2:]);y=gpu[0].signals(gpu[1],torch.tensor(tick,device='cuda'),*gpu[2:]);self.assertTrue(torch.equal(x,y.cpu()))
            torch.testing.assert_close(cpu[0].ratios,gpu[0].ratios.cpu(),rtol=1e-5,atol=2e-7)
            torch.testing.assert_close(cpu[0].export_state()['memory'],gpu[0].export_state()['memory'],rtol=1e-5,atol=3e-7)
        p,d,pos,opened,prev=gpu;p.reset();index=torch.tensor(0,device='cuda');out=torch.empty((1,3,19),device='cuda',dtype=torch.int16)
        for _ in range(3):p.signals(d,index,pos,opened,prev,out)
        p.reset();torch.cuda.synchronize();g=torch.cuda.CUDAGraph()
        with torch.cuda.graph(g):p.signals(d,index,pos,opened,prev,out)
        p.reset();g.replay();torch.cuda.synchronize();first=p.export_state();g.replay();torch.cuda.synchronize();self.assertFalse(torch.equal(first['memory'],p.export_state()['memory']))
    @unittest.skipUnless(torch.cuda.is_available(),'CUDA required')
    def test_fused_finance_matches_stop_oracle(self):
        from research.vectorized_backtest.v54.protected_transition import transition
        args,v=fixture('cuda',8,19);args[0][...,0]=10;args[0][...,1]=9.5;args[0][...,2]=8.9;args[0][...,3]=11;args[0][...,4]=10;args[0][:,:,::3]=0
        args[6]=torch.arange(19,device='cuda',dtype=torch.int16)[None,None].expand(1,8,19)|32
        r=torch.ones((1,8,19,4),device='cuda',dtype=torch.float64);r[...,3]=.02;r[:,:,::5,3]=float('nan')
        bid=v(9.9);ask=v(10.1);valid=v(True,torch.bool);valid[:,:,::7]=False
        expected=step(*[x.cpu() if isinstance(x,torch.Tensor) else x for x in args],risk_protection=True,ratios=r.cpu(),bid=bid.cpu(),ask=ask.cpu(),quote_valid=valid.cpu())
        actual=transition(*args,.2,300.,r,0.,bid,ask,valid)
        for x,y in zip(actual,expected):torch.testing.assert_close(x.cpu(),y,rtol=1e-12,atol=1e-9)

    @unittest.skipUnless(torch.cuda.is_available(),'CUDA required')
    def test_real_shared_plan_compiled_replay_matches_eager_cpu(self):
        from research.vectorized_backtest.v54.evaluator import replay_cohort,Execution
        from research.vectorized_backtest.v42.windows_process import quiet_compilers
        quiet_compilers();members=self.members();results=[]
        for device,backend in (('cpu','eager'),('cuda','compile')):
            p,d,pos,opened,prev=self.setup_plan(device,members,u=3)
            d.device=torch.device(device);d.clocks=10;d.listing_ids=[0,1,2];d.quote_coverage={'maximum_age_seconds':1.}
            d.tensors=dict(mark=torch.full((10,3),10.,device=device,dtype=torch.float64),bid=torch.full((10,3),9.99,device=device,dtype=torch.float64),ask=torch.full((10,3),10.01,device=device,dtype=torch.float64),quote_valid=torch.ones((10,3),device=device,dtype=torch.bool),observed=torch.ones((10,3),device=device,dtype=torch.bool),membership=torch.ones((10,3),device=device,dtype=torch.bool),history_ids=torch.zeros((10,3),device=device,dtype=torch.int64))
            d.swing_bank=lambda ms:(torch.full((1,1),9.7,device=device,dtype=torch.float64),torch.zeros(len(ms),device=device,dtype=torch.int64))
            result=replay_cohort([d],members,[p],trace=True,backend=backend,episode_capacity=1024,replay_block=4,execution=Execution(fee_per_share=.005,minimum_order_fee=1.))[0]
            results.append(result);p.close()
        for key in ('net_pnl','closed_positions','maximum_position_risk','maximum_stop_distance','drawdown','turnover','terminal_valid'):
            torch.testing.assert_close(results[0][key].cpu(),results[1][key].cpu(),rtol=2e-5,atol=1e-5)

if __name__=='__main__':unittest.main()
