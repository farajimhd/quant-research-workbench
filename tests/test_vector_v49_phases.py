import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import unittest,json
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import numpy as np
import torch
from tests.test_vector_v49_whole_network import parent
from research.vectorized_backtest.v49.genome import Individual
from research.vectorized_backtest.v49.lineage import next_population,program_key
from research.vectorized_backtest.v49.local_runner import CompactPedigree
from research.vectorized_backtest.v49.ownership import action_mask,audit,quotas
from research.vectorized_backtest.v49.objectives import local_panel,isolate_panel,Objective
from research.vectorized_backtest.v49.identity import digest

DAY='2026-08-13';A=DAY+':security:a';B=DAY+':security:b'
MAPPING=dict(day=DAY,listing_ids=['a','b'],security_ids=['security:a','security:b'])


def reproduction_fixture(owned=True):
    n=80;members=[]
    for i in range(n):
        a=parent(i%2)
        members.append(Individual({s:replace(net,threshold=-.8+1.6*i/n,size_bias=-.7+1.4*i/n) for s,net in a.networks.items()},a.policy,a.minimum_age,A if i<n//2 else B) if owned else Individual({s:replace(net,threshold=-.8+1.6*i/n,size_bias=-.7+1.4*i/n) for s,net in a.networks.items()},a.policy,a.minimum_age))
    pedigree=CompactPedigree(3);ids=pedigree.birth(members,1,[(-1,-1)]*n,['fresh']*n)
    m=dict(episode_entry_clocks=[[1]]*n,episode_exit_clocks=[[10]]*n,episode_listings=[[0 if i<n//2 else 1] for i in range(n)],closed_positions=[1]*n,capital_dollar_seconds=[100]*n,turnover=[1000]*n,net_pnl=list(range(1,n+1)))
    return members,ids,pedigree,m


class PhaseTests(unittest.TestCase):
    def test_controller_checkpoint_resume_and_both_phase_paths(self):
        from unittest.mock import patch
        from research.vectorized_backtest.v49.local_runner import run_session
        from research.vectorized_backtest.v49.identity import source_hash
        from research.vectorized_backtest.v49.evaluator import Execution
        from research.vectorized_backtest.v42.publication import write_json
        runtime=Path('D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v49/qualification/tests');runtime.mkdir(parents=True,exist_ok=True)
        with TemporaryDirectory(dir=runtime) as tmp:
            root=Path(tmp);inp=root/'inputs'/DAY;hist=root/'history'/DAY;inp.mkdir(parents=True);hist.mkdir(parents=True)
            listings=[dict(listing_id='a',security_id='security:a'),dict(listing_id='b',security_id='security:b')]
            write_json(inp/'complete.json',dict(listings=listings));write_json(hist/'complete.json',dict(listing_ids=[0,1]))
            class Worker:
                loads=1;uploads=1
                def __init__(self,c,*args):self.c=c
                def load(self,day):return SimpleNamespace(identity=dict(input_sha256='i',history_sha256='h',indicator_sha256='d',enriched_sha256='e'),receipt={'listings':listings},listing_ids=np.array([0,1]))
                def close(self):pass
                def run(self,day,next_day,payload,generation,token,width):
                    members=[Individual.restore(p) for p in payload];n=len(members);m={k:[] for k in ('net_pnl','closed_positions','winning_positions','filled_batches','drawdown','maximum_position_risk','maximum_stop_distance','terminal_valid','capital_dollar_seconds','stop_risk_dollar_seconds','turnover','worst_position_pnl','episode_profits','episode_listings','episode_entry_clocks','episode_exit_clocks','ticker_pnl','open_episode_listings','open_episode_realized')}
                    for x in members:
                        indexes=[0] if x.case_owner==A else [1] if x.case_owner==B else [0,1]
                        profit=1+int(program_key(x)[:5],16)%99;values=[float(profit)]*len(indexes)
                        row=dict(net_pnl=sum(values),closed_positions=len(values),winning_positions=len(values),filled_batches=len(values),drawdown=0.,maximum_position_risk=1.,maximum_stop_distance=.1,terminal_valid=True,capital_dollar_seconds=100.,stop_risk_dollar_seconds=0.,turnover=1000.,worst_position_pnl=min(values),episode_profits=values,episode_listings=indexes,episode_entry_clocks=[1]*len(values),episode_exit_clocks=[2]*len(values),ticker_pnl={listings[j]['listing_id']:v for j,v in zip(indexes,values)},open_episode_listings=[],open_episode_realized=[])
                        for k in m:m[k].append(row[k])
                    write_json(Path(self.c['output'])/f'generation-{generation:04d}'/day/'receipt.json',dict(listing_ids=[0,1],metrics=m,case_owners=[x.case_owner for x in members],population_sha256=token,validation_opened=False,execution=self.c['execution'],full_session=True))
                    return dict(wall_seconds=.001)
            def initialize(rng,count,probe=None):
                members,_,_,_=reproduction_fixture(False)
                return members[:count],dict(initial_families=['random']*count)
            cfg=dict(inputs=str(root/'inputs'),history=str(root/'history'),indicator_root=None,enriched_root=None,input_receipts={DAY:dict(inputs='i',history='h')},indicator_receipts={DAY:'d'},enriched_receipts={DAY:'e'},authorization='v49-training',source_sha256=source_hash(),initialization_probe='fixture',training_manifest='fixture',execution=Execution().__dict__,backend='eager',device='cpu',batch_size=32,capture_gib=4.,host_reserve_gib=0.,host_admission_gib=0.,phase=0)
            with patch('research.vectorized_backtest.v42.calibration.load_probe',return_value=dict(metadata=dict(days=[DAY]),sha256='fixture')),patch('research.vectorized_backtest.v49.local_runner.initialize',side_effect=initialize):
                result=run_session(dict(cfg,phase=1),DAY,root/'session/phase-1/local'/DAY,2,32,2020,worker_factory=Worker,initial_multiplier=2)
                self.assertEqual(result['status'],'complete')
                self.assertEqual(run_session(dict(cfg,phase=1),DAY,root/'session/phase-1/local'/DAY,2,32,2020,worker_factory=Worker,initial_multiplier=2)['status'],'complete')
                state=json.loads((root/'session/phase-1/local'/DAY/'checkpoint.json').read_bytes())['state']
                self.assertTrue(all('case_owner' not in p for p in state['population']))
                from research.vectorized_backtest.v49.full_seed import session_export
                exported=session_export(root/'session',DAY,dict(phase1_generations=2,source_sha256=source_hash(),objective=dict(drawdown_weight=.2,loss_weight=.5)))
                self.assertGreater(len(exported['records']),0)
                with self.assertRaisesRegex(ValueError,'only unrestricted'):run_session(cfg,DAY,root/'forbidden',2,32,2020,worker_factory=Worker,initial_multiplier=2)

if __name__=='__main__':unittest.main()
