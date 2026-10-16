"""V24 pure-profit objective, sixfold shared initialization and sealed resume."""
import json
from pathlib import Path
from dataclasses import replace
import numpy as np
import pytest
from test_vector_v23 import metrics as baseline_metrics

def metrics(profits):
    m=baseline_metrics(profits)
    m.update(maximum_position_risk=[0.]*len(profits),maximum_stop_distance=[0.]*len(profits))
    return m

from research.vectorized_backtest.v24.lineage import program_key
from research.vectorized_backtest.v24.objectives import score,Objective,local_panel


def test_pure_net_profit_keeps_losses_zero_activity_and_risk_checks():
    m=metrics([[100.,-25.,10.],[-50.],[]])
    m.update(drawdown=[100000.,2.,0.],maximum_position_risk=[299.,1.,0.],maximum_stop_distance=[.19,.1,0.])
    actual=score([m,m],[128,20,0])
    np.testing.assert_array_equal(actual['score'],[170.,-100.,0.])
    np.testing.assert_array_equal(actual['cost'],[-170.,100.,0.])
    assert actual['global_eligible'].tolist()==[True,True,True]
    assert not actual['concentration_deduction'].any()
    assert not actual['components']['drawdown_penalty'].any()
    m['maximum_position_risk'][0]=301.
    assert not score([m],[0,0,0])['global_eligible'][0]
    with pytest.raises(ValueError,match='forbidden'):replace(Objective(),drawdown_weight=.05).validate()


def test_initial_multiplier_and_imported_population_fail_closed(tmp_path):
    from research.vectorized_backtest.v24.run_search import run
    for multiplier in [0,65,1.5,True]:
        with pytest.raises(ValueError,match='multiplier'):run('unused','unused',tmp_path,initial_multiplier=multiplier)
    with pytest.raises(ValueError,match='imported'):run('unused','unused',tmp_path,initial_population={})


def test_capabilities_and_v21_breeding_are_retained():
    root=Path(__file__).resolve().parents[1]/'research/vectorized_backtest'
    assert (root/'v24/lineage.py').read_text(encoding='utf-8')==(root/'v21/lineage.py').read_text(encoding='utf-8').replace('v21','v24').replace('V21','V24')
    for name in ('genome.py','expressions.py','search_space.py','features.py','feature_gather.py','resident_features.py','program.py','evaluator.py','protected_transition.py'):
        assert (root/'v24'/name).read_text(encoding='utf-8')==(root/'v23'/name).read_text(encoding='utf-8').replace('v23','v24').replace('V23','V24'),name


def test_validation_cannot_change_successors(monkeypatch,tmp_path):
    from io import StringIO
    from rich.console import Console
    import research.vectorized_backtest.v24.run_search as search
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
    interrupt_once=[False]
    evaluated=[]
    class Lanes:
        def __init__(self,config,*a):
            assert config['capture_gib']==8.
            self.config=config
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def release_plans(self):pass
        def generation(self,days,population,generation,token):
            evaluated.append((self.config['split'],generation,len(population),tuple(days)))
            if self.config['split']=='validation' and interrupt_once[0]:
                (Path(self.config['output']).parent/'STOP').touch();interrupt_once[0]=False
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
    assert any(role=='training' and gen==1 and size==384 and len(days)==24 for role,gen,size,days in evaluated)
    assert any(role=='training' and gen==2 and size==64 and len(days)==24 for role,gen,size,days in evaluated)
    validation_multiplier[0]=1;interrupt_once[0]=True
    assert search.run('train','validation',tmp_path/'c',population_size=64,generations=2,batch_size=4,device='cpu',backend='eager',capture_gib=8.)==130
    paused=json.loads((tmp_path/'c/checkpoint.json').read_text())
    assert paused['state']['completed']==1 and len(paused['state']['population'])==64
    before=len(evaluated);(tmp_path/'c/STOP').unlink()
    assert search.run('train','validation',tmp_path/'c',population_size=64,generations=2,batch_size=4,device='cpu',backend='eager',capture_gib=8.)==0
    resumed=json.loads((tmp_path/'c/checkpoint.json').read_text())['state']
    for key in ('population','ids','pedigree','rng'):assert resumed[key]==first[key]
    assert all(gen==2 for role,gen,size,days in evaluated[before:])

    from research.vectorized_backtest.v24.wandb_metrics import packet
    summary=packet(tmp_path/'a',1)
    assert summary['generation']==1 and 'validation/score' in summary
    from research.vectorized_backtest.v24.ranking_display import RankingObserver,MODES
    write_json(tmp_path/'a/status.json',dict(validation_feedback=False,training_ranking_generation=2,validation_generation=2))
    observer=RankingObserver(tmp_path/'a')
    for split in ('training','validation'):
        for mode in MODES:
            observer.set_view(split,mode);observer.refresh()
            snapshot=observer.read()
            assert snapshot['_display_split']==split and snapshot['_display_sort']==mode
            assert len(snapshot['_display_rows'])>0
            from research.vectorized_backtest.v24.terminal import render
            for width,height in ((110,38),(310,85)):
                output=StringIO();console=Console(file=output,width=width,height=height,color_system=None)
                console.print(render(snapshot,width=width,height=height,view='validation' if split=='validation' else 'financial'))
                assert 'sort:' in output.getvalue() and max(map(len,output.getvalue().splitlines()))<=width
    assert all(r['operation']=='fresh' for r in first['pedigree'][:10])
    assert all('v7_source' not in r for r in first['pedigree'])
    assert (tmp_path/'a/initialization.json').is_file()
    contract=json.loads((tmp_path/'a/checkpoint.json').read_text())['contract']
    assert len(contract['training']['days'])==24
    assert contract['selection']['global_fraction']==1.
    assert contract['maximum_rule_nodes']==128
    assert contract['objective_identity']=='pure-net-profit-v1'
    initial=json.loads((tmp_path/'a/initialization.json').read_text())
    assert initial['random_count']==384 and initial['past_results_loaded'] is False
    seal=json.loads((tmp_path/'a/training/generation-0001/complete.json').read_text())
    assert len(seal['session_receipts'])==24
    diversity=json.loads((tmp_path/'a/training/generation-0001/diversity.json').read_text())
    assert diversity['global_slots']==64 and diversity['local_slots']==0
    assert not (tmp_path/'a/local').exists() and not (tmp_path/'a/global').exists()

    # Completed observations remain integrity checked on resume, without selection feedback.
    assert search.run('train','validation',tmp_path/'a',population_size=64,generations=2,batch_size=4,device='cpu',backend='eager',capture_gib=8.)==0
    receipt=tmp_path/'a/validation/generation-0001/2026-09-01/receipt.json'
    receipt.write_text('{}',encoding='utf-8')
    with pytest.raises(ValueError,match='Sealed validation receipt changed'):
        search.run('train','validation',tmp_path/'a',population_size=64,generations=2,batch_size=4,device='cpu',backend='eager',capture_gib=8.)
