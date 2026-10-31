import io
import time
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
from rich.console import Console

from research.vectorized_backtest.v60 import quality_metrics as q
from research.vectorized_backtest.v60 import quality_panel as ui
from research.vectorized_backtest.v60.quality_worker import read_view
from research.vectorized_backtest.v60.publication import write_json


def metrics():
    return dict(net_pnl=[0.,0.,0.,100.,-20.],drawdown=[0.,0.,0.,10.,30.],
        closed_positions=[0,0,0,1,1],winning_positions=[0,0,0,1,0],
        maximum_position_risk=[0.]*5,maximum_stop_distance=[0.]*5,terminal_valid=[True]*5,
        episode_profits=[[],[],[],[100.],[-20.]],episode_listings=[[],[],[],[7],[8]],
        episode_entry_clocks=[[],[],[],[10],[20]],episode_exit_clocks=[[],[],[],[20],[40]],
        admission_entry_signals=[0,2,3,4,5],admission_entry_requests=[0,0,2,3,4],
        admission_entry_first_fills=[0,0,0,1,1])


def test_activity_concentration_commissions_and_objective_parity():
    from research.vectorized_backtest.v60.full_common import aggregate
    m=metrics();s=q.extract(m,np.array([0,0,0,2,3]));p=q.combine([s,s])
    np.testing.assert_allclose(p['score'],aggregate([m,m])['score'])
    assert p['top_day_share'][3]==.5 and np.isnan(p['top_day_share'][0])
    assert p['top_ticker_share'][3]==.5
    assert p['before_fees'][3]==204
    records=[dict(serial=i,node_count=4,complexity=7,parents=[-1,-1]) for i in range(5)]
    stats=q.summary(p,range(5),records,['a','a','b','c','d'],[s,s],2,2)
    assert (stats['inactive'],stats['no_signal'],stats['blocked'],stats['unfilled'])==(3,1,1,1)
    assert stats['median_holding_seconds']==15
    assert stats['duplicate_genomes']==1 and stats['active_edges']['median']==3
    assert stats['traded_sessions_histogram']=={'0':3,'2':2}


def test_partial_position_is_active_and_missing_fees_stay_missing():
    m=metrics();m['admission_entry_first_fills'][0]=1
    s=q.extract(m);p=q.combine([s])
    records=[dict(serial=i,node_count=1,complexity=1) for i in range(5)]
    stats=q.summary(p,range(5),records,list('abcde'),[s],1,24)
    assert stats['inactive']==2 and p['sessions_traded'][0]==1
    assert stats['fees']['median'] is None and stats['complete'] is False


def test_generalization_uses_training_identity_and_normalizes_scope():
    p=q.combine([q.extract(metrics())]);v={k:np.array(a,copy=True) for k,a in p.items()}
    v['score']=np.array([1000,0,0,-50,300]);v['net_pnl']=np.array([1000,0,0,-50,300])
    r=q.generalization(p,v,list(range(5)),2,1)
    assert r['leaders'][0]['validation_net_per_session']==-50
    assert r['leaders'][0]['train_net_per_session']==50
    assert r['positive_to_negative_pct']==100


def test_initial_candidates_are_not_survivors(tmp_path):
    write_json(tmp_path/'candidates-0-training.json',[dict(serial=1,new_offspring=False,groups=[],sessions={})])
    assert len(read_view(tmp_path,0,'training')['rows'])==1
    assert read_view(tmp_path,0,'training',cohort='survivors')['rows']==[]


def test_commission_scan_groups_by_journal_not_candidate_hash(tmp_path):
    import polars as pl
    from research.vectorized_backtest.v60.quality_worker import fees
    pl.DataFrame(dict(candidate=[0,0,1],fees=[1.,2.,4.])).write_parquet(tmp_path/'orders.parquet')
    write_json(tmp_path/'complete.json',dict(pages=[dict(file='orders.parquet')],candidate_sha256=['a','b']))
    refs=[dict(receipt=str(tmp_path/'complete.json'),candidate_index=i,sha256=key) for i,key in enumerate(['a','b'])]
    np.testing.assert_array_equal(fees(dict(net_pnl=[0,0],execution_orders=refs),dict(campaign=str(tmp_path))),[3,4])


def model():
    m=SimpleNamespace(view='quality',selection=0,job=dict(training_days=['d'],validation_days=['v']))
    report=dict(generation=0,sealed=False,roles={},generalization={},reproduction={})
    m.quality=SimpleNamespace(index=dict(history=[report],updated=time.time()),error=None,
        request=lambda *args:dict(report=report,rows=[]),candidate=lambda *args:{})
    m.remember=Mock();ui.setup(m);return m


def test_all_pages_render_compact_normal_and_navigation():
    m=model()
    for width,height in ((80,24),(100,30),(180,50)):
        for page in range(1,6):
            m.quality_page=page
            out=io.StringIO();console=Console(file=out,width=width,height=height,color_system=None)
            console.print(ui.render(m,width,height))
            assert 'V60' in out.getvalue()
            assert len(out.getvalue().splitlines())==height
    ui.key(m,'t',30);assert m.quality_role=='validation'
    ui.key(m,'i',30);assert m.quality_generation==0
    ui.key(m,'c',30);assert m.quality_generation is None
    assert ui.key(m,'back',30) is False
    assert ui.key(m,'d',30) is False
