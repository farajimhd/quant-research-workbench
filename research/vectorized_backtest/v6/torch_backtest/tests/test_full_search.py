import json
import pytest
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
    assert run_generations(spec,10,2,evaluate,tmp_path,workers=8)==0
    before=(tmp_path/'checkpoint.json').read_bytes()
    assert len(calls)==60
    assert run_generations(spec,10,2,evaluate,tmp_path,workers=8)==0
    assert len(calls)==60 and (tmp_path/'checkpoint.json').read_bytes()==before
    assert json.loads(before)['completed_generations']==2

@pytest.mark.parametrize('workers',[0,9,True])
def test_worker_budget_rejected_before_contract_or_preparation(tmp_path,workers):
    class Untouched:
        def contract(self,*args):raise AssertionError('Contract must not run')
        def prepare_pass(self,*args,**kwargs):raise AssertionError('Preparation must not run')
    output=tmp_path/'rejected'
    with pytest.raises(ValueError,match='workers'):
        run_generations({},10,1,Untouched(),output,workers=workers)
    assert not output.exists()


def test_inactive_filter_changes_parents_without_changing_winner(tmp_path,monkeypatch):
    from research.vectorized_backtest.v6.torch_backtest import runtime,full_search
    def mkdir(p):p.mkdir(parents=True,exist_ok=True);return p
    monkeypatch.setattr(runtime,'require_runtime',mkdir);monkeypatch.setattr(full_search,'require_runtime',mkdir)
    seen=[]
    def migrate(rng,population,rank,*args):
        seen.append(rank)
        return population
    monkeypatch.setattr(full_search,'migrate',migrate)
    def evaluate(session,population,destination):
        n=len(population)
        return dict(day=session['day'],population_sha256=population_hash(population),candidate_indices=list(range(n)),full_session=True,validation_opened=False,
            metrics=dict(net_pnl=[0.]*n,drawdown=[0.]*n,stop_risk_dollar_seconds=[0.]*n,capital_dollar_seconds=[0.]*n,filled_batches=[0.]*n,terminal_valid=[True]*n,inactivity_fraction=[1.]*n))
    spec=dict(training=[dict(day=str(i)) for i in range(30)],validation=[dict(day='sealed')])
    assert run_generations(spec,10,2,evaluate,tmp_path,workers=8)==0
    assert len(seen)==1 and len(seen[0])==5 and len(set(seen[0]))==5
    seal=json.loads((tmp_path/'generation-0001/complete.json').read_text())
    expected=min(range(10),key=lambda i:(-seal['ranking']['score'][i],i))
    assert json.loads((tmp_path/'generation-0001/winner.json').read_text())['candidate']==expected


def test_resume_after_sealed_pass_repeats_parent_rng_exactly(tmp_path,monkeypatch):
    from research.vectorized_backtest.v6.torch_backtest import runtime,full_search
    def mkdir(p):p.mkdir(parents=True,exist_ok=True);return p
    monkeypatch.setattr(runtime,'require_runtime',mkdir);monkeypatch.setattr(full_search,'require_runtime',mkdir)
    spec=dict(training=[dict(day=str(i)) for i in range(30)],validation=[dict(day='sealed')])
    calls=[];parents=[];original=full_search.migrate
    def evaluate(session,population,destination):
        calls.append(str(destination));n=len(population)
        return dict(day=session['day'],population_sha256=population_hash(population),candidate_indices=list(range(n)),full_session=True,validation_opened=False,
            metrics=dict(net_pnl=[0.]*n,drawdown=[0.]*n,stop_risk_dollar_seconds=[0.]*n,capital_dollar_seconds=[0.]*n,filled_batches=[0.]*n,terminal_valid=[True]*n,inactivity_fraction=[1.]*n))
    def interrupted(rng,population,rank,*args):
        parents.append(rank)
        raise RuntimeError('Witness interruption after parent RNG selection')
    monkeypatch.setattr(full_search,'migrate',interrupted)
    with pytest.raises(RuntimeError,match='Witness interruption'):
        run_generations(spec,10,2,evaluate,tmp_path/'resumed',workers=8)
    assert len(calls)==30
    def resumed(rng,population,rank,*args):
        parents.append(rank)
        return original(rng,population,rank,*args)
    monkeypatch.setattr(full_search,'migrate',resumed)
    assert run_generations(spec,10,2,evaluate,tmp_path/'resumed',workers=8)==0
    assert len(calls)==60 and parents[0]==parents[1]
    assert run_generations(spec,10,2,evaluate,tmp_path/'uninterrupted',workers=8)==0
    assert (tmp_path/'resumed/checkpoint.json').read_bytes()==(tmp_path/'uninterrupted/checkpoint.json').read_bytes()
