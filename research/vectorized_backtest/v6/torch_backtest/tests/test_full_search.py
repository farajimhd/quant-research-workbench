import json
from research.vectorized_backtest.v6.torch_backtest.full_search import run_generations
from research.vectorized_backtest.v6.torch_backtest.training_pass import population_hash


def test_generation_resume_reuses_all_training_receipts_and_rng(tmp_path,monkeypatch):
    from research.vectorized_backtest.v6.torch_backtest import runtime,full_search
    def mkdir(p):p.mkdir(parents=True,exist_ok=True);return p
    monkeypatch.setattr(runtime,'require_runtime',mkdir);monkeypatch.setattr(full_search,'require_runtime',mkdir)
    spec=dict(training=[dict(day=str(i)) for i in range(30)],validation=[dict(day='sealed')])
    calls=[]
    def evaluate(session,population,destination):
        calls.append(session['day']);n=len(population)
        return dict(day=session['day'],population_sha256=population_hash(population),candidate_indices=list(range(n)),full_session=True,validation_opened=False,
            metrics=dict(net_pnl=[1.]*n,drawdown=[0.]*n,stop_risk_dollar_seconds=[0.]*n,capital_dollar_seconds=[0.]*n,filled_batches=[1.]*n,terminal_valid=[True]*n,inactivity_fraction=[0.]*n))
    assert run_generations(spec,10,2,evaluate,tmp_path)==0
    before=(tmp_path/'checkpoint.json').read_bytes()
    assert len(calls)==60
    assert run_generations(spec,10,2,evaluate,tmp_path)==0
    assert len(calls)==60 and (tmp_path/'checkpoint.json').read_bytes()==before
    assert json.loads(before)['completed_generations']==2
