import copy
import numpy as np
from research.vectorized_backtest.v66.full_common import aggregate,prepare_summary
from research.vectorized_backtest.v66.robust_score import ticker_component


def session(seed):
    rng=np.random.default_rng(seed);n=12
    profits=[rng.normal(0,20,100+i) for i in range(n)]
    opened=[[float(rng.normal())] for _ in range(n)]
    return dict(net_pnl=np.array([sum(p)+o[0] for p,o in zip(profits,opened)]),
        drawdown=np.arange(n,dtype=float),closed_positions=np.array([len(p) for p in profits]),
        winning_positions=np.array([sum(p>0) for p in profits]),maximum_position_risk=np.ones(n),
        maximum_stop_distance=np.ones(n),terminal_valid=np.ones(n,bool),blocked_requests=np.arange(n),
        episode_profits=profits,episode_listings=[rng.integers(1,8,len(p)) for p in profits],
        episode_entry_clocks=[np.arange(len(p)) for p in profits],
        episode_exit_clocks=[np.arange(len(p))+5 for p in profits],
        open_episode_realized=opened,open_episode_listings=[[2] for _ in range(n)])


def test_vectorized_ledger_scoring_matches_v64_exactly():
    from research.vectorized_backtest.v64.robust_score import ticker_component as original
    for seed in range(4):
        m=session(seed)
        np.testing.assert_array_equal(ticker_component(m),original(m))


def test_streamed_panels_match_boundary_aggregation_exactly(monkeypatch):
    from research.vectorized_backtest.v66 import full_common
    raw=[session(i) for i in range(5)];expected=aggregate(raw)
    prepared=[prepare_summary(copy.deepcopy(m)) for m in raw]
    monkeypatch.setattr(full_common,'_original_aggregate',lambda *a,**k:(_ for _ in ()).throw(AssertionError('Rescanned ledger')))
    actual=aggregate(prepared)
    for key in expected:np.testing.assert_array_equal(actual[key],expected[key])


def test_cached_founder_descriptors_preserve_exact_selection():
    from research.vectorized_backtest.v66.founder_selection import select,session_descriptor
    from test_vector_v66_founder_pool import evidence
    ordered,panel=evidence();before,report=select(ordered,panel,8)
    for m in ordered:m['_founder_descriptor']=session_descriptor(m)
    after,again=select(ordered,panel,8)
    assert before==after and report==again


def test_readmission_receipts_remain_hash_bound(tmp_path):
    import json,pytest
    from research.vectorized_backtest.v66.completed_replays import restore
    from research.vectorized_backtest.v66.distributed_assets import sha
    p=tmp_path/'receipt.json'
    receipt=dict(day='2026-01-05',split='training',population_sha256='token',full_session=True,
                 execution={},validation_opened=False,case_owners=[None,None],metrics={'net_pnl':[1,2]})
    p.write_text(json.dumps(receipt))
    job=dict(execution={},founder_readmission={'completed_replays':{'training/0/2026-01-05':dict(path=str(p),sha256=sha(p))}})
    metrics,_=restore(job,tmp_path/'new','training',['2026-01-05'],0,'token',2)
    assert metrics[0]['net_pnl']==[1,2]
    receipt['metrics']['net_pnl'][0]=100;p.write_text(json.dumps(receipt))
    with pytest.raises(ValueError,match='receipt changed'):
        restore(job,tmp_path/'new','training',['2026-01-05'],0,'token',2)
