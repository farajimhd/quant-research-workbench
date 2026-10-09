import threading
from time import sleep
import numpy as np
import pytest
from research.vectorized_backtest.v6.torch_backtest.training_pass import full_training_pass,population_hash
from research.vectorized_backtest.v6.torch_backtest.evolution import sample
from research.vectorized_backtest.v6.torch_backtest.genome import StrategySpace


def test_full_thirty_session_barrier_and_bounded_independent_evaluators(tmp_path,monkeypatch):
    # Runtime output policy is tested separately; this unit isolates the barrier.
    from research.vectorized_backtest.v6.torch_backtest import runtime
    monkeypatch.setattr(runtime,'require_runtime',lambda p:p.mkdir(parents=True,exist_ok=True))
    pop=sample(np.random.default_rng(2),StrategySpace(),2);token=population_hash(pop)
    days=[dict(day=str(i)) for i in range(30)]
    guard=threading.Lock();active=0;peak=0;finished=[]
    def evaluate(session,members,path):
        nonlocal active,peak
        with guard:active+=1;peak=max(peak,active)
        assert members is not pop and members[0] is not pop[0]
        sleep(.002)
        with guard:active-=1;finished.append(session['day'])
        metrics={k:[0.,0.] for k in ('drawdown','stop_risk_dollar_seconds','capital_dollar_seconds','filled_batches','inactivity_fraction')}
        metrics.update(net_pnl=[1.,-1.],terminal_valid=[True,True])
        return dict(day=session['day'],population_sha256=token,candidate_indices=[0,1],full_session=True,validation_opened=False,metrics=metrics)
    rank,results=full_training_pass(days,[],pop,evaluate,tmp_path,workers=2)
    assert peak==2 and len(finished)==len(results)==30
    assert rank['total_pnl'].tolist()==[30.,-30.]
    assert rank['session_tail_count'].tolist()==[6.,6.]
    assert (tmp_path/'complete.json').exists()


def test_partial_panel_rejected_before_evaluation(tmp_path):
    with pytest.raises(ValueError,match='thirty'):
        full_training_pass([{'day':'a'}],[],[object()],lambda *a:None,tmp_path)
