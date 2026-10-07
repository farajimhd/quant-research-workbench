"""Controller resume contract using deterministic evaluator doubles, no market data."""
import json
from pathlib import Path
import torch
import pytest
from research.vectorized_backtest.v5.torch_backtest import staged_search as controller
from research.vectorized_backtest.v5.torch_backtest.runtime import code_hash,file_hash


def test_stages_full_checkpoints_and_interrupted_resume_are_exact(tmp_path,monkeypatch):
    spec=tmp_path/'sessions.json';spec.write_text(json.dumps(dict(training=[dict(day=f'day{i:02d}') for i in range(30)],validation=[dict(day=f'sealed{i}') for i in range(6)])))
    schedule=tmp_path/'schedule.json';schedule.write_text(json.dumps([dict(end_generation=2,population=20,sessions=3,archive_top=3,archive_random=2),dict(end_generation=4,population=10,sessions=6,archive_top=2,archive_random=1)]))
    qualification=tmp_path/'qualification.json';qualification.write_text(json.dumps(dict(status='passed',code_hash=code_hash(),sessions_sha256=file_hash(spec),batch_size=128)))
    monkeypatch.setattr(controller,'preflight',lambda spec:None)
    monkeypatch.setattr(controller,'searchable_features',lambda *_:None)
    class Evaluator:
        def __init__(self,*_):pass
        def close(self):pass
    monkeypatch.setattr(controller,'BatchedEvaluator',Evaluator)
    evaluations=[];interrupt=[False]
    def evaluate(sessions,population,evaluator,folder,emit):
        evaluations.append((len(sessions),len(population)))
        if interrupt[0]:interrupt[0]=False;raise InterruptedError('test interruption before a durable panel')
        n=len(population);result=[]
        for day in sessions:
            metrics={k:[0.]*n for k in ('drawdown','stop_risk_dollar_seconds','capital_dollar_seconds','filled_batches','closed_positions','winning_positions','losing_positions','gross_profit','gross_loss','positions_opened','fill_count','open_positions','sold_share_seconds','sold_shares')}
            metrics.update(net_pnl=[-float(i+1) for i in range(n)],terminal_valid=[True]*n,closed_position_duration_samples=[[] for _ in range(n)])
            result.append(metrics)
        return result,[]
    monkeypatch.setattr(controller,'evaluate_panel',evaluate)
    def args(output):return ['--sessions',str(spec),'--schedule',str(schedule),'--qualification',str(qualification),'--output',str(output),'--device','cpu','--execute']
    uninterrupted=tmp_path/'uninterrupted';assert controller.main(args(uninterrupted))==0
    assert [n for n,_ in evaluations]==[3,3,30,6,6,30]
    assert not (uninterrupted/'frozen_winner.json').exists()
    assert json.loads((uninterrupted/'status.json').read_text())['validation_status']=='SEALED'
    resumed=tmp_path/'resumed';interrupt[0]=True
    assert controller.main(args(resumed))==130
    assert not (resumed/'owner.lock').exists()
    assert controller.main(args(resumed)+['--resume'])==0
    a=json.loads((uninterrupted/'finalist.json').read_text());b=json.loads((resumed/'finalist.json').read_text())
    assert a['winner']==b['winner'] and a['score']==b['score']
    assert json.loads((uninterrupted/'checkpoint.json').read_text())==json.loads((resumed/'checkpoint.json').read_text())
