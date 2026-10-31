from unittest.mock import Mock
from pathlib import Path
from research.vectorized_backtest.v60.campaign_progress import Model


def test_full_results_wait_for_both_splits_and_pin_complete_snapshot():
    model=object.__new__(Model);model.full_data=None
    model.job=dict(training_days=['t'],validation_days=['v'])
    partial=dict(generation=2,loading=True,panels={'training:t':{}},aggregates={'training':{'generation':2}})
    complete=dict(generation=1,panels={'training:t':{},'validation:v':{}},aggregates={'training':{'generation':1}})
    model.global_snapshot=Mock(return_value=partial)
    assert model.full_report()['candidates']==[] and model.full_data is None
    model.global_snapshot.return_value=complete
    assert model.full_report()['generation']==1
    model.global_snapshot.return_value=partial
    assert model.full_report()['generation']==1


def test_full_candidate_detail_ignores_partial_live_and_includes_validation():
    model=object.__new__(Model);model.full_mode=True;model.initial_mode=False;model.day=None
    model.aggregate_mode=True;model.aggregate_role='training';model.selected=dict(serial=7,generation=1)
    model.report=Mock(return_value={'generation':1});model.target=lambda:Path('observer')
    model.read=Mock(return_value={})
    panel=dict(score=[1.],net_pnl=[2.],gross_losses=[0.],drawdown=[1.])
    model.full_data=dict(generation=1,panels={key:dict(ids=[7],panel=panel) for key in ('training:t','validation:v')})
    model.live_data=dict(generation=2,panels={'training:t':dict(ids=[7],panel=panel)})
    rows=model.candidate_sessions()
    assert [r['session'] for r in rows]==['training:t','validation:v']
