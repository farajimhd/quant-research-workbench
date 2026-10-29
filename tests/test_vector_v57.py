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
    def test_snapshot_exact_canonical_and_mutation_detection(self):
        from research.vectorized_backtest.v57.population_snapshot import Snapshot,envelope,dumps
        from research.vectorized_backtest.v57.identity import digest
        members=sample(np.random.default_rng(11),3);snap=Snapshot(members)
        self.assertEqual(snap.token,digest([m.payload() for m in members]))
        self.assertEqual(envelope(dict(z=1),population=snap.raw),dumps(dict(z=1,population=[m.payload() for m in members])))
        ids=np.arange(3);state=np.random.default_rng(7).bit_generator.state
        raw,key=snap.successor(ids,state)
        self.assertEqual(key,digest(dict(population=[m.payload() for m in members],ids=ids.tolist(),rng=state)))
        members[0].minimum_age['exit']=123
        with self.assertRaises(ValueError):snap.verify(members)

    def test_publication_process_errors_propagate(self):
        from research.vectorized_backtest.v57.publication_lane import Lane
        lane=Lane()
        try:
            future=lane.submit(int,'not-an-integer')
            with self.assertRaises(ValueError):future.result(timeout=20)
            with self.assertRaises(ValueError):lane.check()
        finally:
            with self.assertRaises(ValueError):lane.close()

    def test_validation_reuse_never_admits_unknown_graph(self):
        from research.vectorized_backtest.v57.validation_cache import validation_scope,certificate
        members=sample(np.random.default_rng(19),2)
        with validation_scope(capacity=32768):
            for member in members:member.validate()
            approved=certificate(members)
        invalid=Network(members[0].networks['entry'].nodes,threshold=2.)
        with validation_scope(capacity=32768,certificate=approved):
            self.assertIs(members[0].networks['entry'].validate(),members[0].networks['entry'])
            with self.assertRaises(ValueError):invalid.validate()

    def test_cold_resume_keeps_validated_training_cache(self):
        import json,tempfile
        from types import SimpleNamespace
        from research.vectorized_backtest.v57.paired_replay import evaluate
        from research.vectorized_backtest.v57.identity import digest
        root=Path('D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v57/cold-cache-tests');root.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as tmp:
            r=Path(tmp);output=r/'training';cache=output/'result-cache';cache.mkdir(parents=True)
            (cache/'2026-08-13.json').write_text(json.dumps(dict(identity=dict(source='same'),results={},results_sha256=digest({}))))
            worker=SimpleNamespace(config=dict(output=str(output),source_sha256='same'),resident_results=None,population_preparer=None)
            class ReachedReplay(Exception):pass
            def run(*args):
                self.assertEqual(worker.last_result_day,'2026-08-13')
                self.assertEqual(worker.resident_results,({'source':'same'},{}))
                raise ReachedReplay()
            worker.run=run;members=sample(np.random.default_rng(89),2)
            controller=SimpleNamespace(worker=worker,root=r,paired_probe=dict(mutants=members[1:]))
            payload=[members[0].payload()]
            with self.assertRaises(ReachedReplay):evaluate(controller,members[:1],2,'2026-08-13',1,payload,digest(payload))
            self.assertEqual(worker.config['output'],str(output))

    def test_trim_and_best_day(self):
        a=metrics([[100.,20.,-5.]],[[1,1,1]])
        b=metrics([[30.,-10.]],[[1,1]])
        # ticker A=10; B=-20; session costs=108+8 minus best profit115 =>1; retain both drawdowns.
        p=aggregate([a,b]);self.assertEqual(p['ticker_component'][0],-10);self.assertEqual(p['session_component'][0],1);self.assertEqual(p['score'][0],-9)
        self.assertEqual(aggregate([a])['score'][0],3)
    def test_all_losses_not_discarded(self):
        p=aggregate([metrics([[-5.,-10.]],[[1,2]])]);self.assertEqual(p['ticker_component'][0],-30);self.assertEqual(p['session_component'][0],-32)
    def test_empty_and_single_win(self):
        p=aggregate([metrics([[],[50.]],[[],[1]])]);np.testing.assert_array_equal(p['score'],[-2.,-2.])
    def test_local_reproduction_uses_same_trimmed_cost(self):
        from research.vectorized_backtest.v57.objectives import local_panel
        m=metrics([[100.,20.,-5.],[-10.,-5.]],[[0,0,0],[0,0]])
        m.update(ticker_pnl=[{'0':115.},{'0':-15.}],stop_risk_dollar_seconds=[0.,0.],capital_dollar_seconds=[0.,0.])
        panel=local_panel([m]);expected=aggregate([m])['score']
        for key in ('selection_score','specialist'):np.testing.assert_array_equal(panel[key],expected)
        for key in ('values','case_scores'):np.testing.assert_array_equal(panel[key],expected[None])
        self.assertEqual(expected[0],3.)

    def test_recurrence_rejected(self):
        with self.assertRaises(ValueError):Network((Neuron((-1,0),(1.,1.),0.),)).validate()
        for m in sample(np.random.default_rng(42),32):
            self.assertTrue(all(s>=0 for n in m.networks.values() for node in n.nodes for s in node.sources))

    def test_packed_append_matches_full_preparation(self):
        from research.vectorized_backtest.v57.network_pack import pack_networks,concatenate_packed
        members=sample(np.random.default_rng(31),4)
        expected=pack_networks(members);actual=concatenate_packed(pack_networks(members[:2]),pack_networks(members[2:]))
        for a,b in zip(actual['values'],expected['values']):np.testing.assert_array_equal(a,b)
        for key in ('operations','edge_counts','offsets_cpu'):np.testing.assert_array_equal(actual[key],expected[key])
        self.assertEqual(actual['total_nodes'],expected['total_nodes'])

    def test_position_receipt_alignment_and_sorting(self):
        import json,tempfile,hashlib
        from research.vectorized_backtest.v57.position_details import load
        from research.vectorized_backtest.v57.campaign_progress import sorted_rows
        root=Path('D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v57/position-tests');root.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as tmp:
            r=Path(tmp);folder=r/'global/generation-0002';folder.mkdir(parents=True)
            receipt=r/'receipt.json';receipt.write_text(json.dumps(dict(population_sha256='pool',day='2026-08-13',split='training',listing_ids=[42],metrics=dict(episode_profits=[[5.,-2.]],episode_listings=[[0,0]],episode_entry_clocks=[[2,9]],episode_exit_clocks=[[7,11]],open_episode_listings=[[]],open_episode_realized=[[]]))))
            evidence=dict(population_sha256='pool',ids=[71],receipts={'2026-08-13':dict(receipt=str(receipt),receipt_sha256=hashlib.sha256(receipt.read_bytes()).hexdigest())})
            (folder/'training.json').write_text(json.dumps(evidence))
            rows=load(r,2,71,'training:2026-08-13','unused')
            self.assertEqual([v['listing_id'] for v in rows],[42,42]);self.assertEqual(rows[0]['duration'],5)
            self.assertEqual(sorted_rows(rows,'net_pnl',False)[0]['net_pnl'],-2.)
            receipt.write_text('{}')
            with self.assertRaises(ValueError):load(r,2,71,'training:2026-08-13','unused')
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
