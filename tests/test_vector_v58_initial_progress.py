from pathlib import Path
from threading import Event
from unittest.mock import patch
from research.vectorized_backtest.v58.initial_progress import InitialData


def test_initial_report_uses_rescored_panel_and_sealed_ancestry():
    panel=dict(score=[190.],net_pnl=[100.],gross_losses=[0.],drawdown=[10.],closed_positions=[1.],winning_positions=[1.],eligible=[True])
    evidence=dict(ids=[7],panel=panel,receipts={'day':dict(receipt='receipt.json',receipt_sha256='r')},score_version='concentration-v1',coefficient=.5)
    def read(path,expected=None):
        name=Path(path).name
        if name=='manifest.json':return dict(seal='source/global/generation-0001/complete.json',seal_sha256='s',training='source/global/generation-0001/training.json',training_sha256='t')
        if name=='complete.json':return dict(evaluated_pedigree_sha256='p')
        if name=='evaluated-pedigree.json':return [dict(serial=7,operation='template',node_count=12,founder_groups=['template'])]
        if name=='training.json':return evidence
        raise AssertionError(path)
    job=dict(founder_restart=dict(manifest='manifest.json',sha256='m'),training_days=['day'])
    data=InitialData(job,Path('observer'),Event())
    with patch('research.vectorized_backtest.v58.initial_progress.read_verified',side_effect=read):data.load()
    assert data.error is None
    assert data.data['candidates'][0]['score']==190.
    assert data.data['candidates'][0]['nodes']==12
    assert data.data['candidates'][0]['initial_founder']
    assert data.data['groups'][0]['original_founders']==1
    assert data.data['coverage']==1 and data.source_root==Path('source')


def test_initial_candidate_session_recomputes_v58_score():
    data=InitialData(dict(training_days=['day']),Path('observer'),Event())
    data.evidence=dict(ids=[7],receipts={'day':dict(receipt='receipt.json',receipt_sha256='r')});data.request=7
    metrics=dict(net_pnl=[100.],drawdown=[10.],closed_positions=[1.],winning_positions=[1.],maximum_position_risk=[1.],maximum_stop_distance=[.1],terminal_valid=[True],episode_profits=[[100.]],episode_exit_clocks=[[2]],episode_listings=[[0]])
    receipt=dict(day='day',split='training',validation_opened=False,metrics=metrics)
    with patch('research.vectorized_backtest.v58.initial_progress.read_verified',return_value=receipt):data.load_sessions()
    assert data.error is None
    assert data.sessions==[dict(session='training:day',score=190.,net_pnl=100.,gross_losses=0.,drawdown=10.)]
