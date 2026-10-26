import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import unittest,json
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch,Mock
import numpy as np
import torch
from research.vectorized_backtest.v56.genome import Individual,Network,Neuron,INPUTS,STAGES
from research.vectorized_backtest.v56.full_common import aggregate
from research.vectorized_backtest.v56.identity import digest
from tests import test_vector_v56_whole_network as helpers
from tests.test_vector_v53_pilot import metrics

def wide(n):
    leaves=(n+1)//2;nodes=[Neuron((0,),(.1,),0.) for _ in range(leaves)];pending=list(range(leaves));cursor=0
    while len(nodes)<n:
        take=min(2,len(pending)-cursor);sources=tuple(INPUTS+i for i in pending[cursor:cursor+take]);cursor+=take
        nodes.append(Neuron(sources,(.3,)*take,0.));pending.append(len(nodes)-1)
    return Network(tuple(nodes),threshold=0.).validate()

class BudgetTests(unittest.TestCase):
    def test_per_action_and_total_limits_are_independent_and_1024(self):
        from research.vectorized_backtest.v56.config import MAX_NODES,MAX_STRATEGY_NODES,MAX_DEPTH
        self.assertEqual((MAX_NODES,MAX_STRATEGY_NODES,MAX_DEPTH),(1024,1024,512))
        wide(1024)
        with self.assertRaises(ValueError):wide(1025)
        a=helpers.parent(0);nets=dict(a.networks);nets['entry']=wide(1020)
        good=replace(a,networks=nets).validate();self.assertEqual(sum(len(n.nodes) for n in good.networks.values()),1024)
        with self.assertRaises(ValueError):replace(a,networks=dict(nets,entry=wide(1021))).validate()
        from research.vectorized_backtest.v53.genome import Individual as Old
        with self.assertRaises(ValueError):Old.restore(good.payload())
    def test_composition_can_cross_the_old_512_boundary(self):
        from research.vectorized_backtest.v56.whole_network import cross,node_cost
        a=helpers.parent(0);b=replace(a,networks=dict(a.networks,entry=wide(250))).validate();a=replace(a,networks=dict(a.networks,entry=wide(700))).validate()
        child,_=cross(a,b,'xor',('entry',));self.assertEqual(node_cost(a,b,('entry',)),960);self.assertEqual(sum(len(n.nodes) for n in child.networks.values()),960)
    def test_selection_caps_removed_but_cost_and_terminal_validity_retained(self):
        m=metrics(-10.,2);m['drawdown']=[50.,50.];m['maximum_position_risk']=[5000.,5000.];m['maximum_stop_distance']=[.8,.8];m['terminal_valid']=[True,False]
        result=aggregate([m]);self.assertEqual(result['score'].tolist(),[-120.,-120.]);self.assertEqual(result['eligible'].tolist(),[True,False])
        self.assertEqual(result['maximum_position_risk'].tolist(),[5000.,5000.])
        m['maximum_position_risk'][0]=float('nan')
        with self.assertRaises(ValueError):aggregate([m])
    def test_local_objective_has_no_hidden_legacy_selection_filter(self):
        from research.vectorized_backtest.v56.objectives import local_panel
        m=metrics(100.,1);m.update(ticker_pnl=[{'a':100.}],stop_risk_dollar_seconds=[10.],maximum_position_risk=[5000.],maximum_stop_distance=[.8])
        result=local_panel([m]);self.assertEqual(result['eligible'].tolist(),[[True]]);self.assertEqual(result['values'].tolist(),[[96.]])
    def test_receipt_reuse_cannot_silently_import_another_population(self):
        from research.vectorized_backtest.v56.initial_reuse import import_receipt
        with patch('research.vectorized_backtest.v56.initial_reuse.verify',return_value=dict(population_sha256='other')):
            with self.assertRaises(ValueError):import_receipt({},[],'2026-08-13')
    def test_founder_reuse_invokes_no_worker_replay(self):
        from research.vectorized_backtest.v56.pilot import Pilot
        from research.vectorized_backtest.v56.initial_reuse import sha
        root=Path('D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v56/qualification/tests');root.mkdir(parents=True,exist_ok=True)
        members=[helpers.parent(0)]*6144;payload=[m.payload() for m in members];token=digest(payload)
        with TemporaryDirectory(dir=root) as tmp:
            path=Path(tmp)/'receipt.json';receipt=dict(population_sha256=token,full_session=True,validation_opened=False,split='training',execution={},case_owners=[None]*6144,metrics={'net_pnl':[0.]*6144})
            path.write_text(json.dumps(receipt));bound=dict(population_sha256=token,receipts={'2026-08-13':dict(receipt=str(path),receipt_sha256=sha(path))})
            c=Pilot.__new__(Pilot);c.root=Path(tmp);c.job=dict(training_days=['2026-08-13'],execution={});c.worker=Mock();c.publish=lambda **kw:None
            with patch('research.vectorized_backtest.v56.initial_reuse.verify',return_value=bound):values,refs=c.evaluate('training',['2026-08-13'],members,1)
            c.worker.run.assert_not_called();self.assertEqual(len(values[0]['net_pnl']),6144);self.assertTrue(refs['2026-08-13']['authorized_founder_reuse'])
    @unittest.skipUnless(torch.cuda.is_available(),'CUDA required')
    def test_above_512_nodes_cpu_cuda_parity(self):
        a=helpers.parent(0);a=replace(a,networks=dict(a.networks,entry=wide(1020))).validate()
        for (x,r),(y,s) in zip(helpers.signals([a],'cpu',2),helpers.signals([a],'cuda',2)):
            self.assertTrue(torch.equal(x,y));torch.testing.assert_close(r,s,rtol=2e-6,atol=2e-7)

if __name__=='__main__':unittest.main()
