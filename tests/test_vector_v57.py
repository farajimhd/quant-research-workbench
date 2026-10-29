import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
from pathlib import Path
import unittest
import numpy as np
from research.vectorized_backtest.v57.full_common import aggregate
from research.vectorized_backtest.v57.genome import sample,Neuron,Network
from research.vectorized_backtest.v57.progress_global import combined

def metrics(profits,listings):
    n=len(profits)
    return dict(net_pnl=[sum(p) for p in profits],drawdown=[2.]*n,closed_positions=[len(p) for p in profits],winning_positions=[sum(v>0 for v in p) for p in profits],maximum_position_risk=[1.]*n,maximum_stop_distance=[.1]*n,terminal_valid=[True]*n,episode_profits=profits,episode_listings=listings,episode_exit_clocks=[list(range(len(p))) for p in profits])

class V57Tests(unittest.TestCase):
    def test_trim_and_best_day(self):
        a=metrics([[100.,20.,-5.]],[[1,1,1]])
        b=metrics([[30.,-10.]],[[1,1]])
        # ticker A:115-5-100=10; B:20-10-30=-20; remaining session B:20-10-2=8
        p=aggregate([a,b]);self.assertEqual(p['ticker_component'][0],-10);self.assertEqual(p['session_component'][0],8);self.assertEqual(p['score'][0],-2)
        self.assertEqual(aggregate([a])['score'][0],10)
    def test_all_losses_not_discarded(self):
        p=aggregate([metrics([[-5.,-10.]],[[1,2]])]);self.assertEqual(p['ticker_component'][0],-30);self.assertEqual(p['session_component'][0],-32)
    def test_empty_and_single_win(self):
        p=aggregate([metrics([[],[50.]],[[],[1]])]);np.testing.assert_array_equal(p['score'],[-2.,0.])
    def test_recurrence_rejected(self):
        with self.assertRaises(ValueError):Network((Neuron((-1,0),(1.,1.),0.),)).validate()
        for m in sample(np.random.default_rng(42),32):
            self.assertTrue(all(s>=0 for n in m.networks.values() for node in n.nodes for s in node.sources))
    def test_opposite_split_alignment(self):
        train=aggregate([metrics([[100.,20.]],[[1,1]])]);val=aggregate([metrics([[-10.]],[[1]])])
        data=dict(generation=1,panels={'training:a':dict(ids=[7],panel=train),'validation:b':dict(ids=[7],panel=val)})
        record=dict(serial=7,parents=[-1,-1],operation='fresh',node_count=5)
        report=combined(data,[record],1);self.assertEqual(report['candidates'][0]['opposite_net'],-10)
        data['panels']['validation:b']['ids']=[8]
        with self.assertRaises(ValueError):combined(data,[record],1)

    def test_identity_cache_and_variation(self):
        from research.vectorized_backtest.v57.identity_cache import identity_scope
        from research.vectorized_backtest.v57.lineage import program_key
        from research.vectorized_backtest.v57.composition import mutation,cross
        from research.vectorized_backtest.v42.schedule import Proposal
        m=sample(np.random.default_rng(10),4)
        keys=[program_key(x) for x in m]
        with identity_scope():self.assertEqual(keys,[program_key(x) for x in m])
        crossed=0
        for k in range(30):
            child,_=mutation(Proposal(b'test',('m',k)),m[k%4],1+k%5);child.validate()
            try:child,_=cross(Proposal(b'test',('x',k)),m[k%4],m[(k+1)%4])
            except ValueError:continue  # Existing masks may reject empty or over-budget children.
            child.validate();crossed+=1
        self.assertGreater(crossed,0)

    def test_pilot_resume_preserves_state_and_contract(self):
        import json,hashlib,tempfile
        from research.vectorized_backtest.v57.resume_authority import accept_checkpoint
        from research.vectorized_backtest.v57.identity import digest
        root=Path('D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v57/resume-tests');root.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as tmp:
            old=Path(tmp)/'old.json';cp=Path(tmp)/'checkpoint.json'
            job=dict(version='v57-pilot-v2',source_sha256='old',objective={'drawdown_weight':1},population=1024,training_days=['2026-08-13'])
            old.write_text(json.dumps(job));saved=dict(contract={'source_sha256':'old','population':1024},state_sha256='state');cp.write_text(json.dumps(saved))
            sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
            new=dict(job,source_sha256='new',resume_transition=dict(predecessor_job=str(old),predecessor_job_sha256=sha(old),pilot_checkpoint=dict(checkpoint_sha256=sha(cp),state_sha256='state',contract_sha256=digest(saved['contract']))))
            expected=dict(saved['contract'],source_sha256='new');accept_checkpoint(new,cp,saved,expected)
            with self.assertRaises(ValueError):accept_checkpoint(dict(new,population=512),cp,saved,expected)
            cp.write_text('{}')
            with self.assertRaises(ValueError):accept_checkpoint(new,cp,saved,expected)

    def test_terminal_opposite_split_columns(self):
        import io
        from unittest.mock import patch
        from rich.console import Console
        from research.vectorized_backtest.v57.campaign_progress import Model,render
        root=Path('D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v57/panel-check');root.mkdir(parents=True,exist_ok=True)
        job=dict(version='v57-pilot-v2',campaign=str(root),transport=str(root),workstation_share='unused',training_days=['2026-08-13'],validation_days=[],machines={'laptop':{'session_days':['2026-08-13']}})
        m=Model(job);m.aggregate_mode=True;m.view='ranking'
        row=dict(serial=7,score=10.,net_pnl=20.,gross_losses=1.,drawdown=2.,closed_positions=3.,nodes=12,operators=['xor'],opposite_score=-3.,opposite_net=4.,opposite_drawdown=2.,opposite_coverage=12)
        report=dict(candidates=[row],groups=[],coverage=24,total=24,partial=False)
        try:
            with patch.object(m,'report',return_value=report),patch.object(m,'status',return_value={}),patch.object(m,'timing',return_value=dict(last=None,elapsed=None,work=None,eta=None,generation_eta=None)):
                for role in ('training','validation'):
                    m.aggregate_role=role
                    for width,height in ((100,28),(180,45)):
                        out=io.StringIO();Console(file=out,width=width,height=height,color_system=None).print(render(m,width,height));value=out.getvalue()
                        (root/f'{role}-{width}.txt').write_text(value,encoding='utf-8')
                        self.assertIn('Val' if role=='training' else 'Train',value)
                        self.assertIn('4.00',value)
        finally:m.close()

if __name__=='__main__':unittest.main()
