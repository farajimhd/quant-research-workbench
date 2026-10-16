"""Independent profitability gates and unchanged replay boundary."""
import numpy as np
from dataclasses import replace
from research.vectorized_backtest.v23.objectives import score,session_scores,Objective
from research.vectorized_backtest.v18.objectives import score as prior_score
from test_vector_v17_regressions import metrics as original_metrics

def metrics(profits):
    m=original_metrics(profits)
    m["ticker_pnl"]=[{str(i):float(v) for i,v in enumerate(row)} for row in profits]
    return m


def test_global_reward_and_separate_global_local_eligibility():
    # Consistent profit, concentrated outlier, nearly idle, and local specialist.
    profits=[[[10.,10.,10.],([1000.]*3 if d==0 else [-1.]*3),([.001] if d==0 else []),([100.]*3 if d==0 else [])] for d in range(24)]
    ordered=[metrics(p) for p in profits]
    result=score(ordered,[0]*4);old=prior_score(ordered,[0]*4)
    alltrades=[np.concatenate([np.asarray(p[i]) for p in profits]) for i in range(4)]
    from research.vectorized_backtest.v18.objectives import episode_statistics
    trimmed=episode_statistics(alltrades,Objective())['trimmed_profit']
    np.testing.assert_allclose(result['score'],old['score']+.1*trimmed)
    assert result['global_eligible'].tolist()==[True,False,False,False]
    values,eligible=session_scores(ordered,prepared=result)
    assert eligible[0].tolist()==[True,True,False,True]
    assert not eligible[1:,3].any()
    np.testing.assert_allclose(values,old['_local']['values'])

def test_near_zero_and_loss_cannot_be_global_or_local_parents():
    ordered=[metrics([[0.,0.,0.],[-1.,-1.,-1.]]) for _ in range(24)]
    result=score(ordered,[0,0]);assert not result['global_eligible'].any()
    assert not session_scores(ordered,prepared=result)[1].any()

def test_empty_parent_pools_supply_fresh_population_without_relaxing_gates():
    from research.vectorized_backtest.v23.genome import sample
    from research.vectorized_backtest.v23.lineage import Pedigree,next_population,Selection
    rng=np.random.default_rng(19);members=sample(rng,64);pedigree=Pedigree();ids=pedigree.birth(members,1,[(-1,-1)]*64,["fresh"]*64)
    children,newids,report=next_population(rng,members,ids,pedigree,list(range(64)),2,session_values=np.zeros((24,64)),session_eligible=np.zeros((24,64),bool),global_values=np.zeros(64),global_eligible=np.zeros(64,bool))
    assert len(children)==64 and report['fresh_candidates']==64
    assert report['global_elites']==report['local_champions']==0

def test_group_sort_matches_prior_lexsort_exactly():
    from research.vectorized_backtest.v23.objectives import episode_statistics
    from research.vectorized_backtest.v18.objectives import episode_statistics as reference
    rng=np.random.default_rng(1907)
    groups=[[],[0.,-0.,0.,-0.],[1.,1.,-1.,-1.]]+[rng.normal(size=int(n)) for n in rng.integers(0,500,257)]
    actual=episode_statistics(groups,Objective());expected=reference(groups,Objective())
    for key in expected:np.testing.assert_array_equal(actual[key],expected[key])

import pytest
from research.vectorized_backtest.v23.objectives import local_panel
from research.vectorized_backtest.v23.genome import sample
from research.vectorized_backtest.v23.lineage import program_key
from pathlib import Path
import json


def test_sampling_reports_size_coverage_and_twenty_adds():
    from research.vectorized_backtest.v23.initial_population import initialize
    members,report=initialize(np.random.default_rng(2121),128)
    assert max(m.policy.maximum_adds for m in members)==20
    assert min(m.policy.maximum_adds for m in members)==0
    assert report['coverage']['features'] and report['coverage']['windows']
    assert report['sampling'] and report['unique_count']==128
    sizes=list(map(int,report['coverage']['effective_sizes']))
    assert any(3<=s<=9 for s in sizes) and any(10<=s<=19 for s in sizes) and any(20<=s<=32 for s in sizes)


def test_temporal_ratio_is_uncentered_and_typed():
    from research.vectorized_backtest.v23.expressions import temporal_ratio
    from research.vectorized_backtest.v23.features import CATALOG
    from research.vectorized_backtest.v23.program import Op
    index=next(i for i,f in enumerate(CATALOG) if f.name=='market.close')
    rule=temporal_ratio(np.random.default_rng(1),index,5)
    assert [n.op for n in rule.nodes]==[Op.FEATURE,Op.LAG,Op.DIVIDE]
    assert rule.validate(CATALOG,output_unit='ratio')['units'][-1]=='ratio'


def test_movement_projection_changes_only_magnitude_semantics():
    import torch
    from research.vectorized_backtest.v23.resident_features import ResidentFeatures
    from research.vectorized_backtest.v7.resident_features import ResidentFeatures as OldResident
    from research.vectorized_backtest.v23.features import CATALOG
    obj=object.__new__(ResidentFeatures);obj.device=torch.device('cpu');obj.column_plans={}
    history=torch.zeros((2,332),dtype=torch.float32);history[:,180]=torch.tensor([.5,1.]);history[:,0]=9.
    obj.tensors=dict(history=history,feature_row=torch.tensor([0,1]),mark=torch.tensor([10.,20.],dtype=torch.float64))
    columns=[next(i for i,f in enumerate(CATALOG) if f.name.startswith('history.average_move.') and f.name.endswith('fraction_of_close')),
             next(i for i,f in enumerate(CATALOG) if f.name.startswith('history.recent_high.') and f.name.endswith('relative_to_close'))]
    rows=torch.tensor([[0,1,-1]]);ids=torch.tensor([[0,1,0]]);clock=torch.zeros_like(rows)
    old,oldmask=OldResident._values(obj,rows,ids,clock,columns)
    actual,mask=obj._values_reference(rows,ids,clock,columns)
    torch.testing.assert_close(actual[0,:2,0],torch.tensor([.05,.05]),rtol=0,atol=0)
    torch.testing.assert_close(actual[...,1],old[...,1],rtol=0,atol=0)
    assert torch.equal(mask,oldmask) and not mask[0,2].any()


def test_local_trims_full_ticker_totals_not_individual_positions():
    m=metrics([[100.,100.,-50.,10.]])
    m['ticker_pnl']=[{'A':150.,'B':10.}]
    actual=local_panel([m],Objective())
    baseline=local_panel([m],Objective(),trim_basis='position')
    # Two traded tickers: remove .2 * 150 = 30, not .4 * 100 = 40.
    assert actual['trimmed'][0,0]==130.
    assert baseline['trimmed'][0,0]==120.
    assert actual['values'][0,0]==baseline['values'][0,0]+10.
    assert actual['eligible'][0,0]
    # Holding ticker totals fixed, splitting a winning position does not change trim.
    split=metrics([[50.,50.,100.,-50.,10.]])
    split['ticker_pnl']=m['ticker_pnl']
    assert local_panel([split],Objective())['trimmed'][0,0]==130.


def test_local_ticker_trim_keeps_losses_empty_and_reconciles():
    m=metrics([[100.,-50.,-30.],[]])
    m['ticker_pnl']=[{'A':100.,'B':-50.,'C':-30.},{}]
    actual=local_panel([m],Objective())
    np.testing.assert_allclose(actual['trimmed'],[[-10.,0.]])
    assert actual['eligible'].tolist()==[[False,False]]
    m['ticker_pnl'][0]['A']=101.
    with pytest.raises(ValueError,match='reconcile'):local_panel([m],Objective())


def test_v19_selection_and_financial_sources_preserved():
    root=Path(__file__).resolve().parents[1]/'research/vectorized_backtest'
    for name in ('lineage.py','evaluator.py','protected_transition.py','sparse_replay.py','replay_graph.py','variation.py','semantics.py','enriched_math.py','indicator_math.py'):
        assert (root/'v23'/name).read_text(encoding='utf-8')==(root/'v19'/name).read_text(encoding='utf-8').replace('v19','v23').replace('V19','V23'),name
    from research.vectorized_backtest.v23.lineage import Selection
    assert Selection().global_fraction==.5 and Selection().champion_partners==5


def test_global_cost_components_remain_v19_with_local_ticker_selection():
    from research.vectorized_backtest.v19.objectives import score as reference
    ordered=[metrics([[100.,100.,-50.,10.]]) for _ in range(24)]
    for m in ordered:m['ticker_pnl']=[{'A':150.,'B':10.}]
    actual=score(ordered,[50]);expected=reference(ordered,[50])
    for k in ('score','cost','global_eligible','feasible','total_pnl','global_session_trim_removed'):
        np.testing.assert_array_equal(actual[k],expected[k])
    for k in expected['components']:np.testing.assert_array_equal(actual['components'][k],expected['components'][k])
    values,eligible=session_scores(ordered,prepared=actual)
    np.testing.assert_array_equal(values,local_panel(ordered,Objective())['values'])
    assert not np.array_equal(values,expected['_local']['values'])


def test_validation_cannot_change_successors(monkeypatch,tmp_path):
    from io import StringIO
    from rich.console import Console
    import research.vectorized_backtest.v23.run_search as search
    from research.vectorized_backtest.v6.torch_backtest.runtime import write_json,require_runtime
    class Panel:
        def __init__(self,*a):self.generation_samples=[]
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def update(self,**kwargs):pass
        def begin_generation(self,*a):pass
        def begin_evaluation(self,*a):pass
        def publish(self):pass
    validation_multiplier=[1]
    class Lanes:
        def __init__(self,config,*a):
            assert config['capture_gib']==8.
            self.config=config
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def release_plans(self):pass
        def generation(self,days,population,generation,token):
            for day in days:
                profits=[[float(int(program_key(m)[:6],16)%1000+1)] for m in population]
                if self.config['split']=='validation':profits=[[p[0]*validation_multiplier[0]] for p in profits]
                root=require_runtime(Path(self.config['output'])/f'generation-{generation:04d}'/day)
                write_json(root/'receipt.json',dict(metrics=metrics(profits),full_session=True,population_sha256=token))
            return []
    from pathlib import Path
    monkeypatch.setattr(search,'CampaignProgress',Panel);monkeypatch.setattr(search,'SessionLanes',Lanes)
    monkeypatch.setattr(search,'bind_dataset',lambda path,role:dict(days=[f'2026-08-{i:02d}' for i in range(1,25)] if role=='training' else ['2026-09-01'],
        inputs='unused',history='unused',indicator_root='unused',enriched_root='unused',receipts={'day':'bound'}))
    for suffix,multiplier in [('a',1),('b',-100)]:
        validation_multiplier[0]=multiplier
        search.run('train','validation',tmp_path/suffix,population_size=64,generations=2,batch_size=4,device='cpu',backend='eager',capture_gib=8.)
    assert json.loads((tmp_path/'a'/'checkpoint.json').read_text())['contract']['capture_gib']==8.
    first=json.loads((tmp_path/'a'/'checkpoint.json').read_text())['state']
    second=json.loads((tmp_path/'b'/'checkpoint.json').read_text())['state']
    for key in ('population','ids','pedigree','rng'):assert first[key]==second[key]
    assert first['validation_ranking']!=second['validation_ranking']
    from research.vectorized_backtest.v23.wandb_metrics import packet
    summary=packet(tmp_path/'a',1)
    assert summary['generation']==1 and 'validation/score' in summary
    from research.vectorized_backtest.v23.ranking_display import RankingObserver,MODES
    write_json(tmp_path/'a/status.json',dict(validation_feedback=False,training_ranking_generation=2,validation_generation=2))
    observer=RankingObserver(tmp_path/'a')
    for split in ('training','validation'):
        for mode in MODES:
            observer.set_view(split,mode);observer.refresh()
            snapshot=observer.read()
            assert snapshot['_display_split']==split and snapshot['_display_sort']==mode
            assert len(snapshot['_display_rows'])>0
            from research.vectorized_backtest.v23.terminal import render
            for width,height in ((110,38),(310,85)):
                output=StringIO();console=Console(file=output,width=width,height=height,color_system=None)
                console.print(render(snapshot,width=width,height=height,view='validation' if split=='validation' else 'financial'))
                assert 'sort:' in output.getvalue() and max(map(len,output.getvalue().splitlines()))<=width
    assert all(r['operation']=='fresh' for r in first['pedigree'][:10])
    assert all('v7_source' not in r for r in first['pedigree'])
    assert (tmp_path/'a/initialization.json').is_file()
    contract=json.loads((tmp_path/'a/checkpoint.json').read_text())['contract']
    assert len(contract['training']['days'])==24
    assert contract['selection']['global_fraction']==.5
    assert contract['maximum_rule_nodes']==128
    assert contract['local_profit_trim']=='positive-fractional-top-ticker-v1'
    initial=json.loads((tmp_path/'a/initialization.json').read_text())
    assert initial['random_count']==64 and initial['past_results_loaded'] is False
    seal=json.loads((tmp_path/'a/training/generation-0001/complete.json').read_text())
    assert len(seal['session_receipts'])==24
    diversity=json.loads((tmp_path/'a/training/generation-0001/diversity.json').read_text())
    assert diversity['global_slots']==32 and diversity['local_slots']==32
    assert not (tmp_path/'a/local').exists() and not (tmp_path/'a/global').exists()

    # Completed observations remain integrity checked on resume, without selection feedback.
    assert search.run('train','validation',tmp_path/'a',population_size=64,generations=2,batch_size=4,device='cpu',backend='eager',capture_gib=8.)==0
    receipt=tmp_path/'a/validation/generation-0001/2026-09-01/receipt.json'
    receipt.write_text('{}',encoding='utf-8')
    with pytest.raises(ValueError,match='Sealed validation receipt changed'):
        search.run('train','validation',tmp_path/'a',population_size=64,generations=2,batch_size=4,device='cpu',backend='eager',capture_gib=8.)
