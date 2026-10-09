import numpy as np
import torch
import pytest
from research.vectorized_backtest.v5.torch_backtest.inactivity import area
from research.vectorized_backtest.v5.torch_backtest.stability import score,DollarObjective
from research.vectorized_backtest.v5.torch_backtest.staged import selection_rank

def test_elapsed_cost_integral():
    assert area(3600)==0
    assert area(7200)==720
    assert area(18000)==7200
    assert area(21600)==10800
    assert area(3600)+area(3600)==0

def test_dollar_arithmetic_and_best_session_retained():
    pnl=torch.tensor([[-100.,0.],[-100.,0.],[800.,0.]],dtype=torch.float64)
    zeros=torch.zeros_like(pnl);nodes=torch.tensor([4.,4.]);valid=torch.ones_like(pnl,dtype=torch.bool)
    result=score(pnl,zeros,zeros,zeros,zeros,valid,nodes,config=DollarObjective(),inactivity=torch.tensor([0.,1.]))
    assert result['score'][0].item()==pytest.approx(600-75-3.75)
    assert result['score'][1].item()==pytest.approx(-3.75-30)
    assert result['feasible'].all()

def test_seeded_inactive_selection_retains_half():
    results=[{'filled_batches':[1]+[0]*10}];rank=list(range(11))
    a=selection_rank(np.random.default_rng(3),rank,{},results,DollarObjective())
    b=selection_rank(np.random.default_rng(3),rank,{},results,DollarObjective())
    assert a==b and len(a)==6 and 0 in a


def test_fill_time_restored_to_candidate_and_session_reset(tmp_path):
    from research.vectorized_backtest.v5.torch_backtest.inactivity import panel_metrics
    (tmp_path/'batch_0000').mkdir()
    ledger=torch.zeros((2,2,9),dtype=torch.float64)
    ledger[0,0,0]=3600;ledger[0,0,3]=1
    ledger[0,1,0]=7200;ledger[0,1,3]=-1
    torch.save(dict(ledger=ledger,counts=torch.tensor([2,0])),tmp_path/'batch_0000/fills.pt')
    receipt=dict(execution={'creator_identity':{'session':{'start':'1970-01-01T00:00:00+00:00','end':'1970-01-01T06:00:00+00:00'}}},batch_receipts=[{'directory':'batch_0000'}],candidate_order=[1,0],metrics={})
    result=panel_metrics(receipt,tmp_path)
    assert result['inactivity_seconds']==[10800.,7200.]
    assert result['eligible_seconds']==[21600.,21600.]

def test_full_population_backward_wrap():
    from research.vectorized_backtest.v5.torch_backtest.staged_dashboard import navigate
    status={'top_strategies':[{'rank':i} for i in range(1,4097)]}
    assert navigate(status,'b',height=38)==(4051,81,0)
    assert navigate(status,'n',rank=4089,rank_page=511,height=38)==(1,0,0)


def test_qualification_reuse_rejects_engine_changes(tmp_path):
    import json
    from hashlib import sha256
    from research.vectorized_backtest.v5.torch_backtest.qualification_reuse import verify,source_hash
    old=tmp_path/'old';new=tmp_path/'new';old.mkdir();new.mkdir()
    for root in (old,new):(root/'runner.py').write_text('execution');(root/'stability.py').write_text('old')
    report=tmp_path/'report.json';report.write_text(json.dumps({'status':'passed'}))
    q={'code_hash':source_hash(old),'report':str(report),'report_sha256':sha256(report.read_bytes()).hexdigest()}
    (new/'stability.py').write_text('new')
    assert verify(old,new,q)['profiling_repeated'] is False
    (new/'runner.py').write_text('changed')
    with pytest.raises(ValueError,match='execution changed'):verify(old,new,q)
