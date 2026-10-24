import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import unittest,json
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import numpy as np
import torch
from tests.test_vector_v48_whole_network import parent
from research.vectorized_backtest.v48.genome import Individual
from research.vectorized_backtest.v48.lineage import next_population,program_key
from research.vectorized_backtest.v48.local_runner import CompactPedigree
from research.vectorized_backtest.v48.ownership import action_mask,audit,quotas
from research.vectorized_backtest.v48.objectives import local_panel,isolate_panel,Objective
from research.vectorized_backtest.v48.identity import digest

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
        from research.vectorized_backtest.v48.local_runner import run_session
        from research.vectorized_backtest.v48.identity import source_hash
        from research.vectorized_backtest.v48.phases import promote
        from research.vectorized_backtest.v48.evaluator import Execution
        from research.vectorized_backtest.v42.publication import write_json
        runtime=Path('D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v48/qualification/tests');runtime.mkdir(parents=True,exist_ok=True)
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
            cfg=dict(inputs=str(root/'inputs'),history=str(root/'history'),indicator_root=None,enriched_root=None,input_receipts={DAY:dict(inputs='i',history='h')},indicator_receipts={DAY:'d'},enriched_receipts={DAY:'e'},authorization='v48-training',source_sha256=source_hash(),initialization_probe='fixture',training_manifest='fixture',execution=Execution().__dict__,backend='eager',device='cpu',batch_size=32,capture_gib=4.,host_reserve_gib=0.,host_admission_gib=0.,phase=0)
            with patch('research.vectorized_backtest.v42.calibration.load_probe',return_value=dict(metadata=dict(days=[DAY]),sha256='fixture')),patch('research.vectorized_backtest.v48.local_runner.initialize',side_effect=initialize):
                result=run_session(cfg,DAY,root/'p0',2,32,2020,worker_factory=Worker,initial_multiplier=1)
                self.assertEqual(result['status'],'complete')
                self.assertEqual(run_session(cfg,DAY,root/'p0',2,32,2020,worker_factory=Worker,initial_multiplier=1)['status'],'complete')
                promoted=promote(root/'p0',root/'promotion.json',32,2)
                self.assertGreater(promoted['specialist_count'],0)
                result=run_session(dict(cfg,phase=1,promotion=str(root/'promotion.json')),DAY,root/'p1',2,32,2020,worker_factory=Worker,initial_multiplier=1)
                self.assertEqual(result['status'],'complete')
                state=json.loads((root/'p1/checkpoint.json').read_bytes())['state']
                self.assertTrue(all('case_owner' not in p for p in state['population']))

    @unittest.skipUnless(torch.cuda.is_available(),'GPU replay qualification')
    def test_owned_compiled_gpu_replay_matches_cpu(self):
        from research.vectorized_backtest.v48.evaluator import replay_cohort
        from research.vectorized_backtest.v48.network import NetworkPlan
        from research.vectorized_backtest.v48.genome import MARKET_INPUTS
        from research.vectorized_backtest.v42.features import CATALOG
        members=[replace(parent(0),case_owner=A),replace(parent(1),case_owner=B)]
        results=[]
        for device,backend in [('cpu','eager'),('cuda','compile')]:
            data=SimpleNamespace(root=Path(DAY),device=torch.device(device),clocks=3,listing_ids=[0,1],
                receipt={'listings':[{'security_id':'security:a','listing_id':'a'},{'security_id':'security:b','listing_id':'b'}]},quote_coverage={'maximum_age_seconds':1.},
                resident_features=SimpleNamespace(tensors={'clocks':torch.arange(3,device=device)}),
                tensors=dict(mark=torch.full((3,2),10.,dtype=torch.float64,device=device),bid=torch.full((3,2),9.9,dtype=torch.float64,device=device),ask=torch.full((3,2),10.1,dtype=torch.float64,device=device),
                             quote_valid=torch.ones((3,2),dtype=torch.bool,device=device),observed=torch.ones((3,2),dtype=torch.bool,device=device),membership=torch.ones((3,2),dtype=torch.bool,device=device),history_ids=torch.zeros((3,2),dtype=torch.int64,device=device)))
            data.swing_bank=lambda members:(torch.full((1,1),9.7,dtype=torch.float64,device=device),torch.tensor([0,0],device=device))
            plan=NetworkPlan(members,device)
            values=torch.full((2,MARKET_INPUTS),.5,device=device);valid=torch.ones((2,len(CATALOG)),device=device,dtype=torch.bool)
            plan._project=lambda *args:(values,valid)
            raw=replay_cohort([data],members,[plan],backend=backend,trace=True)[0]
            metrics={k:v.detach().cpu().tolist() if isinstance(v,torch.Tensor) else v for k,v in raw.items()}
            self.assertEqual(metrics['episode_listings'],[[0],[1]])
            results.append(metrics);plan.close()
        np.testing.assert_allclose(results[0]['net_pnl'],results[1]['net_pnl'],rtol=2e-6,atol=1e-7)

    def test_financial_replay_never_opens_foreign_positions(self):
        from research.vectorized_backtest.v48.evaluator import replay_cohort
        class Plan:
            total_nodes=1
            ratios=torch.ones((1,2,2,3),dtype=torch.float64)
            feedback=torch.zeros((2,2,11),dtype=torch.float32)
            def prepare(self,*args):pass
            def reset(self):self.feedback.zero_()
            def signals(self,data,index,position,opened,previous,out):out.fill_(1 if int(index)==0 else 2)
        data=SimpleNamespace(root=Path(DAY),device=torch.device('cpu'),clocks=3,listing_ids=[0,1],
            receipt={'listings':[{'security_id':'security:a','listing_id':'a'},{'security_id':'security:b','listing_id':'b'}]},quote_coverage={'maximum_age_seconds':1.},
            tensors=dict(mark=torch.full((3,2),10.,dtype=torch.float64),bid=torch.full((3,2),9.9,dtype=torch.float64),ask=torch.full((3,2),10.1,dtype=torch.float64),
                         quote_valid=torch.ones((3,2),dtype=torch.bool),observed=torch.ones((3,2),dtype=torch.bool),membership=torch.ones((3,2),dtype=torch.bool),history_ids=torch.zeros((3,2),dtype=torch.int64)))
        data.swing_bank=lambda members:(torch.full((1,1),9.7,dtype=torch.float64),torch.tensor([0,0]))
        members=[replace(parent(0),case_owner=A),replace(parent(1),case_owner=B)]
        raw=replay_cohort([data],members,[Plan()],trace=True)[0]
        metrics={k:v.tolist() if isinstance(v,torch.Tensor) else v for k,v in raw.items()}
        from research.vectorized_backtest.v48.financial import ticker_totals
        metrics['ticker_pnl']=ticker_totals(metrics,data)
        self.assertEqual(metrics['episode_listings'],[[0],[1]])
        audit(members,metrics,MAPPING)
        panel=isolate_panel(local_panel([metrics],case_maps=[MAPPING]),members)
        self.assertEqual(panel['specialist_targets'],[A,B])

    def test_promotion_selects_earlier_generations_and_discards_fitness(self):
        from research.vectorized_backtest.v48.phases import promote
        from research.vectorized_backtest.v42.publication import write_json,write_checkpoint
        from research.vectorized_backtest.v6.torch_backtest.runtime import file_hash
        members=[replace(parent(0),case_owner=A),replace(parent(1),case_owner=B)]
        pedigree=CompactPedigree(3);ids=pedigree.birth(members,1,[(-1,-1)]*2,['fresh']*2)
        archived=[dict(serial=int(ids[i]),payload=m.payload(),case_owner=m.case_owner,pedigree=pedigree.records[int(ids[i])],source_generation=i+1,first_seen_generation=i+1,case_scores=[10.,0.] if i==0 else [0.,20.]) for i,m in enumerate(members)]
        root=Path('D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v48/qualification/tests');root.mkdir(parents=True,exist_ok=True)
        with TemporaryDirectory(dir=root) as tmp:
            source=Path(tmp)/'phase0';source.mkdir();seals={}
            for g in range(1,4):
                f=source/f'generation-{g:04d}';(f/DAY).mkdir(parents=True)
                for file,data in [('scores.json',dict(ids=ids.tolist(),scores=[10.,20.] if g<3 else [1.,2.])),('births.json',[]),('specialists.json',{}),(DAY+'/receipt.json',dict(generation=g))]:write_json(f/file,data)
                seal=dict(phase=0,ownership_verified=True,receipt_sha256=file_hash(f/DAY/'receipt.json'),scores_sha256=file_hash(f/'scores.json'),births_sha256=file_hash(f/'births.json'),specialists_sha256=file_hash(f/'specialists.json'))
                write_json(f/'complete.json',seal);seals[str(g)]=file_hash(f/'complete.json')
            state=dict(completed=3,specialist_archive=archived,pedigree=list(pedigree.records.values()),next_serial=2,rng=np.random.default_rng(5).bit_generator.state,seals=seals)
            write_checkpoint(source/'checkpoint.json',dict(phase=0,day=DAY,case_inventory=[A,B]),state)
            write_json(source/'complete.json',dict(checkpoint_sha256=file_hash(source/'checkpoint.json')))
            output=Path(tmp)/'promotion.json';result=promote(source,output,target=2,expected_generations=3)
            self.assertEqual([p['source_generation'] for p in result['provenance']],[1,2])
            self.assertEqual(result['specialist_count'],2);self.assertEqual(result['fresh_count'],0)
            self.assertTrue(all('case_owner' not in p for p in result['population']))
            self.assertFalse(result['financial_results_reused'])
            self.assertNotIn('metrics',result)
            self.assertEqual(json.loads(output.read_bytes()),json.loads(json.dumps(promote(source,output,target=2,expected_generations=3))))
            write_json(source/'generation-0001/scores.json',dict(ids=ids.tolist(),scores=[999.,20.]))
            with self.assertRaises(ValueError):promote(source,Path(tmp)/'bad.json',target=2,expected_generations=3)

    def test_penalty_and_session_simultaneous_close_drawdown(self):
        m=dict(net_pnl=[100.],closed_positions=[3],ticker_pnl=[{'a':150.,'b':-50.}],drawdown=[200.],maximum_position_risk=[1.],maximum_stop_distance=[.1],terminal_valid=[True],stop_risk_dollar_seconds=[0.],capital_dollar_seconds=[0.],episode_profits=[[200.,-50.,-50.]],episode_listings=[[0,0,1]],episode_exit_clocks=[[1,2,2]])
        p=local_panel([m],case_maps=[MAPPING])
        self.assertEqual(p['case_losses'].ravel().tolist(),[50.,50.])
        self.assertEqual(p['case_scores'].ravel().tolist(),[122.5,-77.5])
        self.assertEqual(p['selection_score'].tolist(),[45.])
        self.assertEqual(p['global_profit'].tolist(),[100.])

    def test_case_masks_cache_identity_and_cross_parent_rejection(self):
        from research.vectorized_backtest.v48.whole_network import cross
        from research.vectorized_backtest.v48.composition import cross as masked
        from research.vectorized_backtest.v42.schedule import Proposal
        a=replace(parent(0),case_owner=A);b=replace(parent(0),case_owner=B)
        self.assertNotEqual(program_key(a),program_key(b))
        from research.vectorized_backtest.v48.validation_cache import validation_scope
        expected=[program_key(a),program_key(b)]
        with validation_scope():self.assertEqual(expected,[program_key(a),program_key(b)])
        self.assertEqual(Individual.restore(a.payload()).case_owner,A)
        item=SimpleNamespace(root=Path(DAY),device='cpu',listing_ids=[0,1],receipt={'listings':[{'security_id':'security:a'},{'security_id':'security:b'}]})
        self.assertEqual(action_mask(item,[a,b],3).tolist(),[[[31,30,30],[30,31,30]]])
        with self.assertRaises(ValueError):cross(a,b,'union')
        with self.assertRaises(ValueError):masked(Proposal(b'1'*32,(1,),{}),a,b)
        with self.assertRaises(ValueError):action_mask(item,[a,parent(0)],2)
        with self.assertRaises(ValueError):audit([a],dict(episode_listings=[[1]],ticker_pnl=[{'b':2.}]),MAPPING)

    def test_phase0_allocation_reproduction_and_deterministic_resume(self):
        outputs=[]
        for resumed in (False,True):
            members,ids,pedigree,m=reproduction_fixture()
            rng=np.random.default_rng(2020)
            if resumed:
                members=[Individual.restore(x.payload()) for x in members]
                pedigree=CompactPedigree(3,json.loads(json.dumps(list(pedigree.records.values()))),pedigree.next_serial)
                rng.bit_generator.state=json.loads(json.dumps(rng.bit_generator.state))
            n=len(members);v=np.arange(1,n+1,dtype=float);eligible=np.ones(n,bool)
            out,newids,report=next_population(rng,members,ids,pedigree,list(range(n)),2,session_values=v[None],session_eligible=eligible[None],global_values=v,global_eligible=eligible,target_size=128,ordered=[m],case_values=np.zeros((2,n)),case_ids=[A,B])
            self.assertEqual(len(out),128);self.assertEqual(sum(report['case_quotas'].values()),128)
            self.assertEqual(report['selection_policy'],'isolated-case-score')
            for i in newids:
                r=pedigree.records[int(i)]
                for p in r['parents']:
                    if p>=0:self.assertEqual(pedigree.records[int(p)]['case_owner'],r['case_owner'])
            outputs.append(digest(dict(population=[x.payload() for x in out],ids=newids.tolist(),rng=rng.bit_generator.state)))
        self.assertEqual(*outputs)

    def test_mixed_stage_has_one_selection_score_pool(self):
        members,ids,pedigree,m=reproduction_fixture(False);n=len(members);v=np.arange(1,n+1,dtype=float);valid=np.ones(n,bool)
        out,newids,report=next_population(np.random.default_rng(9),members,ids,pedigree,list(range(n)),2,session_values=v[None],session_eligible=valid[None],global_values=v,global_eligible=valid,target_size=128,ordered=[m],case_values=None,case_ids=None)
        self.assertEqual(report['selection_policy'],'unified-session-score')
        self.assertEqual(report['case_quotas'],{'SESSION':128})
        self.assertTrue(all(x.case_owner is None for x in out))
        self.assertEqual(report['requested_crossovers'],90)

    def test_empty_case_keeps_exploration_and_no_budget_expansion(self):
        q=quotas([A,B],[A],1024,1)
        self.assertEqual(q,{A:1023,B:1})
        with self.assertRaises(ValueError):quotas([A,B],[],1,1)


if __name__=='__main__':unittest.main()
