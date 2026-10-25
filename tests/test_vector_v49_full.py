import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import unittest,json,io
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import numpy as np
from rich.console import Console
from research.vectorized_backtest.v49.full_common import aggregate
from research.vectorized_backtest.v49.full_controller import Controller
from research.vectorized_backtest.v49.local_runner import CompactPedigree
from research.vectorized_backtest.v49.identity import digest
from research.vectorized_backtest.v49.full_progress import render
from tests.test_vector_v49_phases import reproduction_fixture


def metrics(values,n=1):
    values=list(map(float,values));net=sum(values);count=len(values)
    return dict(net_pnl=[net]*n,closed_positions=[count]*n,winning_positions=[sum(v>0 for v in values)]*n,
        episode_profits=[values]*n,episode_exit_clocks=[list(range(2,2+count))]*n,episode_entry_clocks=[[1]*count]*n,episode_listings=[[0]*count]*n,
        terminal_valid=[True]*n,maximum_position_risk=[10.]*n,maximum_stop_distance=[.1]*n,drawdown=[sum(max(-v,0) for v in values)]*n,capital_dollar_seconds=[100.]*n,turnover=[1000.]*n)


class FullTests(unittest.TestCase):
    def test_open_equity_drawdown_and_ticker_partition_do_not_escape_cost(self):
        from research.vectorized_backtest.v49.objectives import local_panel
        m=metrics([100,-10]);m.update(drawdown=[500.],ticker_pnl=[{'a':90.}],stop_risk_dollar_seconds=[0.])
        result=local_panel([m]);self.assertEqual(result['selection_score'].tolist(),[-15.])
        m['ticker_pnl']=[{'a':100.,'b':-10.}]
        self.assertEqual(local_panel([m])['selection_score'].tolist(),[-15.])
        joint=aggregate([m,m]);self.assertEqual(joint['score'].tolist(),[-30.]);self.assertEqual(joint['drawdown'].tolist(),[1000.])
        m['drawdown']=[-1.]
        with self.assertRaises(ValueError):aggregate([m])

    def test_global_seed_namespaces_session_pedigrees_and_preserves_families(self):
        from research.vectorized_backtest.v49.full_seed import global_seed
        from research.vectorized_backtest.v49.genome import sample
        from research.vectorized_backtest.v49.lineage import program_key
        members,_,_,_=reproduction_fixture(False);exports={}
        for day,offset in [('d1',0),('d2',10)]:
            p=CompactPedigree(3);chosen=members[offset:offset+3];ids=p.birth(chosen,1,[(-1,-1)]*3,['fresh']*3);records=[]
            for i,m in zip(ids,chosen):
                record=dict(p.records[int(i)],ancestors=[[99,1]])
                records.append(dict(payload=m.payload(),serial=int(i),generation=1,pedigree=record,canonical_sha256=program_key(m),source_seal_sha256='sealed'))
            exports[day]=dict(records=records)
        def initialize(rng,n,probe=None):return sample(rng,n),dict(initial_families=['random']*n)
        with patch('research.vectorized_backtest.v42.initial_population.initialize',side_effect=initialize):pool=global_seed(exports,dict(population=10,seed=2020,source_sha256='source'),{})
        self.assertEqual(len(pool['population']),10);self.assertEqual(len(set(pool['ids'])),10);self.assertEqual(pool['expert_count'],6)
        imported=[r for r in pool['pedigree'] if 'source_day' in r]
        a=next(r for r in imported if r['source_day']=='d1');b=next(r for r in imported if r['source_day']=='d2')
        self.assertNotEqual(a['ancestors'][0][0],b['ancestors'][0][0]);self.assertTrue(all('family_contributions' in r for r in pool['pedigree']))

    def test_chronological_joint_cost_and_invalid_session(self):
        p=aggregate([metrics([100,-150]),metrics([200,-10])])
        self.assertEqual(p['net_pnl'].tolist(),[140.]);self.assertEqual(p['gross_losses'].tolist(),[160.]);self.assertEqual(p['drawdown'].tolist(),[160.]);self.assertEqual(p['score'].tolist(),[28.])
        m=metrics([1]);m['terminal_valid']=[False]
        self.assertFalse(aggregate([metrics([1]),m])['eligible'][0])

    def test_full_global_barrier_resume_and_validation_rng_independence(self):
        runtime=Path('D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v49/full-preparation/tests');runtime.mkdir(parents=True,exist_ok=True)
        hashes=[]
        for validation_profit in (20.,-100000.):
            with TemporaryDirectory(dir=runtime) as tmp:
                members,_,_,_=reproduction_fixture(False);members=members[:10];pedigree=CompactPedigree(3);ids=pedigree.birth(members,1,[(-1,-1)]*10,['fresh']*10)
                seed=dict(population=[m.payload() for m in members],ids=ids.tolist(),pedigree=list(pedigree.records.values()),next_serial=10,rng=np.random.default_rng(2020).bit_generator.state)
                c=Controller.__new__(Controller);c.root=Path(tmp);c.stage='';c.sessions={};c.job=dict(population=10,seed=2020,phase2_generations=2,training='fixture',probe_root='fixture',source_sha256='source',objective=dict(drawdown_weight=.2,loss_weight=.5),training_sha256='training',validation_sha256='validation',selection={})
                c.publish=lambda **kwargs:None
                calls=[]
                def evaluate(role,days,population,generation):
                    calls.append((role,generation));return [metrics([10. if role=='training' else validation_profit],len(population)) for _ in days],{}
                c.evaluate=evaluate
                with patch('research.vectorized_backtest.v49.full_seed.global_seed',return_value=seed),patch('research.vectorized_backtest.v42.probes.global_probe',return_value='fixture'),patch('research.vectorized_backtest.v42.calibration.load_probe',return_value={}):
                    c.global_run({},['d1','d2'],['v1'])
                state=json.loads((c.root/'global/checkpoint.json').read_bytes())['state'];hashes.append(digest(dict(population=state['population'],ids=state['ids'],rng=state['rng'])))
                self.assertEqual(calls,[('training',1),('validation',1),('training',2),('validation',2)])
                for g in (1,2):
                    proof=json.loads((c.root/f'global/generation-{g:04d}/validation.json').read_bytes());self.assertEqual(proof['successor_sha256_before'],proof['successor_sha256_after'])
                calls.clear();c.global_run({},['d1','d2'],['v1']);self.assertEqual(calls,[])
                (c.root/'global/generation-0001/training.json').write_text('{}')
                with self.assertRaisesRegex(ValueError,'evidence'):c.global_run({},['d1','d2'],['v1'])
        self.assertEqual(*hashes)

    def test_host_affinity_claims_and_result_hash(self):
        from research.vectorized_backtest.v42.distributed_bus import Bus
        from research.vectorized_backtest.v42.distributed_assets import sha
        runtime=Path('D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v49/full-preparation/tests');runtime.mkdir(parents=True,exist_ok=True)
        with TemporaryDirectory(dir=runtime) as tmp,patch('research.vectorized_backtest.v42.distributed_bus.remote',side_effect=lambda path,share:Path(path)):
            job=dict(transport=tmp,workstation_share='unused',machines={'laptop':{},'workstation':{}});a=Bus(job,'laptop');b=Bus(job,'workstation')
            key=a.submit(dict(kind='session',day='d1',node='laptop'))
            self.assertIsNone(b.claim(dict(pid=1,creation_time=1)));self.assertEqual(a.claim(dict(pid=1,creation_time=1))[0],key)
            c=Controller.__new__(Controller);c.bus=a;p=a.root/'results'/key/'receipt';p.parent.mkdir(parents=True);p.write_text('ok');result=dict(files=[dict(name='receipt',sha256=sha(p))]);self.assertEqual(c.result_file(key,result,'receipt'),p)
            p.write_text('changed')
            with self.assertRaises(ValueError):c.result_file(key,result,'receipt')

    def test_full_console_all_phase_and_compact_states(self):
        job=dict(objective=dict(loss_weight=.75),machines={'laptop':{},'workstation':{}})
        states=[dict(phase='local',sessions={'2026-08-13':dict(node='laptop',phase=0,completed_generations=5,status='running',stage='Replay')},status='running'),dict(phase=2,completed_generations=1,evaluation_split='validation',completed_sessions=2,total_sessions=12,status='running',training_ranking=[],validation_ranking=[]),dict(phase=2,completed_generations=100,status='complete')]
        for width,height in ((120,38),(80,24)):
            for state in states:
                stream=io.StringIO();Console(file=stream,width=width,height=height,color_system=None).print(render(state,job,width,height))
                lines=stream.getvalue().splitlines();self.assertEqual(len(lines),height);self.assertTrue(all(len(x)<=width for x in lines));self.assertIn('V49 FULL',stream.getvalue());self.assertIn('Q closes',stream.getvalue())


if __name__=='__main__':unittest.main()
