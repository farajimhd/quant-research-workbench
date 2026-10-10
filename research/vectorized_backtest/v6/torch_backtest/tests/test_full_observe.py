import json
import pytest
from rich.console import Console
from research.vectorized_backtest.v6.torch_backtest.full_observe_data import FullTrainingView
from research.vectorized_backtest.v6.torch_backtest.runtime import file_hash
from research.vectorized_backtest.v6.torch_backtest.staged_dashboard import render,navigate


def fixture(root,n=55):
    generation=root/'generation-0001';generation.mkdir()
    days=[f'day-{i:02d}' for i in range(30)];receipts={}
    for day in days:
        p=generation/day/'receipt.json';p.parent.mkdir()
        p.write_text(json.dumps(dict(day=day,population_sha256='population',candidate_indices=list(range(n)),full_session=True,validation_opened=False,
            metrics=dict(net_pnl=[1.]*n,drawdown=[2.]*n,stop_risk_dollar_seconds=[3600.]*n,capital_dollar_seconds=[7200.]*n,
                closed_positions=[2]*n,winning_positions=[1]*n,losing_positions=[1]*n,gross_profit=[3.]*n,gross_loss=[2.]*n,
                positions_opened=[2]*n,fill_count=[4]*n,open_positions=[0]*n))))
        receipts[day]=file_hash(p)
    ranking=dict(score=list(range(n)),feasible=[True]*n,components=dict(total_profit=[30.]*n),
        total_pnl=[30.]*n,best_day_pnl=[1.]*n,other_days_pnl=[29.]*n,profitable_day_fraction=[1.]*n,
        median_return=[.0001]*n,tail_loss=[0.]*n,session_tail_mean_pnl=[1.]*n,session_tail_count=[6]*n)
    complete=generation/'complete.json'
    complete.write_text(json.dumps(dict(status='complete',selection_allowed=True,validation_opened=False,training_days=days,
        session_receipts=receipts,population_sha256='population',ranking=ranking)))
    (root/'checkpoint.json').write_text(json.dumps(dict(contract=dict(version='v6-all-training-search-v1',spec='fingerprint',generations=2,population=n,evaluator=dict(batch_size=128)),
        completed_generations=1,last_generation_sha256=file_hash(complete))))
    active=root/'generation-0002';active.mkdir()
    (active/'resident-status.json').write_text(json.dumps(dict(stage='Concurrent captured replay',sessions=days[:8],candidate_offset=0)))
    return complete


def test_completed_all30_rankings_remain_independent_of_active_generation(tmp_path):
    fixture(tmp_path);view=FullTrainingView();status=view.read(tmp_path)
    assert status['completed_generations']==1 and status['completed_sessions']==0
    assert status['top_strategies'][0]['candidate']==55
    m=status['top_strategies'][0]['metrics']
    assert m['total_pnl']==30 and m['other_days_pnl']==29 and m['positions']==60
    assert m['position_win_rate']==.5 and m['profit_factor']==1.5
    assert m['stop_risk_hours']==30 and m['capital_hours']==60
    assert status['stage']=='Concurrent captured replay' and status['validation_status']=='SEALED'
    assert view.read(tmp_path)==status  # Cached read keeps its verified generation.
    assert navigate(status,'n',height=38)==(51,1,0)
    for width,height in ((160,45),(80,24)):
        c=Console(width=width,height=height,color_system=None)
        with c.capture() as out:c.print(render(status,width=width,height=height))
        lines=out.get().splitlines();assert len(lines)==height and max(map(len,lines))<=width
        assert 'Q close' in lines[-1]
        with c.capture() as out:c.print(render(status,width=width,height=height,view='positions'))
        assert 'Positions / fills' in out.get()
        with c.capture() as out:c.print(render({**status,'_page':1 if height>=30 else 0},width=width,height=height))
        assert 'P&L excluding best day' in out.get()


def test_changed_generation_seal_rejected_even_after_cache(tmp_path):
    complete=fixture(tmp_path);view=FullTrainingView();view.read(tmp_path)
    complete.write_text(complete.read_text()+' ')
    with pytest.raises(ValueError,match='seal changed'):view.read(tmp_path)


def test_changed_session_receipt_rejected(tmp_path):
    fixture(tmp_path);view=FullTrainingView();view.read(tmp_path)
    p=tmp_path/'generation-0001/day-00/receipt.json';p.write_text(p.read_text()+' ')
    with pytest.raises(ValueError,match='session receipt changed'):view.read(tmp_path)
    with pytest.raises(ValueError,match='session receipt changed'):FullTrainingView().read(tmp_path)


def test_incomplete_panel_cannot_be_ranked(tmp_path):
    complete=fixture(tmp_path);record=json.loads(complete.read_text());record['training_days'].pop();complete.write_text(json.dumps(record))
    p=tmp_path/'checkpoint.json';saved=json.loads(p.read_text());saved['last_generation_sha256']=file_hash(complete);p.write_text(json.dumps(saved))
    with pytest.raises(ValueError,match='all30'):FullTrainingView().read(tmp_path)


def test_first_generation_has_no_completed_ranking(tmp_path):
    (tmp_path/'checkpoint.json').write_text(json.dumps(dict(contract=dict(version='v6-all-training-search-v1',population=128,generations=8,evaluator=dict(batch_size=128)),completed_generations=0)))
    s=FullTrainingView().read(tmp_path)
    assert not s['top_strategies'] and s['completed_batches']==0 and s['total_batches']==30


def test_restoration_reports_current_family_progress_and_stable_launch_time(tmp_path):
    import os
    fixture(tmp_path)
    (tmp_path/'active.json').write_text(json.dumps(dict(creation_time=1234.5)))
    resident=tmp_path/'generation-0002/resident-status.json'
    resident.write_text(json.dumps(dict(stage='Restoring compiler context 2/6; no full-session evaluation',sessions=['all30'])))
    child=tmp_path/'compiler-priming/family-0001/resident-status.json';child.parent.mkdir(parents=True)
    child.write_text(json.dumps(dict(stage='Build and capture financial broker',day='2026-08-06',validation_opened=False)))
    os.utime(child,(2000000000,2000000000))
    view=FullTrainingView();s=view.read(tmp_path)
    assert s['started_epoch']==1234.5 and s['updated_epoch']==2000000000
    assert s['focus']=='2026-08-06' and 'Build and capture' in s['stage']
    assert s['completed_generations']==1 and s['completed_sessions']==0
    checkpoint=tmp_path/'checkpoint.json'
    checkpoint.write_text(checkpoint.read_text()+' ')
    assert view.read(tmp_path)['started_epoch']==1234.5
    child.write_text(json.dumps(dict(validation_opened=True)))
    with pytest.raises(ValueError,match='opened validation'):view.read(tmp_path)


def test_resumed_progress_counts_durable_batches_without_measurements(tmp_path):
    fixture(tmp_path,n=55)
    cp=tmp_path/'checkpoint.json';saved=json.loads(cp.read_text());saved['population_sha256']='active-population';cp.write_text(json.dumps(saved))
    folder=tmp_path/'generation-0002/day-00/batch-000000';folder.mkdir(parents=True)
    receipt=folder/'receipt.json'
    batch=dict(day='day-00',population_sha256='active-population',candidate_indices=list(range(55)),financial_audit_passed=True,full_session=True,validation_opened=False)
    receipt.write_text(json.dumps(batch));view=FullTrainingView()
    assert view.read(tmp_path)['completed_batches']==1
    assert view.read(tmp_path)['completed_batches']==1
    batch['population_sha256']='wrong';receipt.write_text(json.dumps(batch))
    with pytest.raises(ValueError,match='batch progress'):view.read(tmp_path)


def test_measured_eta_and_normal_width_ranking_columns(tmp_path):
    fixture(tmp_path)
    p=tmp_path/'generation-0002/resident-measurements.json'
    p.write_text(json.dumps([dict(active=8,setup_seconds=8,replay_and_audit_seconds=56,session_timings=[{}]*8),
        dict(active=0,setup_seconds=.1,replay_and_audit_seconds=.01,session_timings=[])]))
    status=FullTrainingView().read(tmp_path)
    assert status['replay_eta']==240 and status['session_eta']==56 and status['average_batch_seconds']==8
    assert status['process_progress'][0]['label']=='Saved / audited batches'
    c=Console(width=132,height=38,color_system=None)
    with c.capture() as out:c.print(render(status,width=132,height=38))
    assert 'Ex-best' in out.get() and 'Tail day' in out.get() and 'Days +%' in out.get()


def test_full_training_position_tail_uses_complete_episodes_and_indices(tmp_path):
    import torch
    from research.vectorized_backtest.v6.torch_backtest.ranking_diagnostics import diagnostics
    complete=fixture(tmp_path,n=2);record=json.loads(complete.read_text())
    ledger=torch.tensor([[1,0,1,1,1,10,.1,0,0],[2,0,1,-1,1,11,.1,0,0],
                         [3,0,2,1,1,10,.1,0,0],[4,0,2,-1,1,9,.1,0,0]],dtype=torch.float64)
    for day in record['training_days']:
        session=complete.parent/day/'receipt.json';folder=session.parent/'batch-000000';folder.mkdir()
        torch.save(dict(ledger=ledger[None].repeat(2,1,1),counts=torch.tensor([4,4])),folder/'fills.pt')
        batch=folder/'receipt.json';batch.write_text(json.dumps(dict(candidate_indices=[0,1],ledger_sha256=file_hash(folder/'fills.pt'),metrics=dict(closed_positions=[2,2]))))
        s=json.loads(session.read_text());s['batch_receipts']=[dict(directory=folder.name,sha256=file_hash(batch))];session.write_text(json.dumps(s));record['session_receipts'][day]=file_hash(session)
    complete.write_text(json.dumps(record))
    result=diagnostics(tmp_path,1,[2],full_training=True)
    assert result[2]['position_tail_count']==12 and result[2]['position_tail_closed_count']==60
    assert result[2]['position_tail_mean_pnl']==pytest.approx(-1.2)
    p=complete.parent/'day-00/receipt.json';s=json.loads(p.read_text());s['validation_opened']=True;p.write_text(json.dumps(s))
    record['session_receipts']['day-00']=file_hash(p);complete.write_text(json.dumps(record))
    with pytest.raises(ValueError,match='bound training'):diagnostics(tmp_path,1,[2],full_training=True)
