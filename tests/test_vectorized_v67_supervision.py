"""Real compiled accounting parity and saved-1b objective boundaries."""
import numpy as np
import pytest
from research.vectorized_backtest.v66 import compact_execution as baseline
from research.vectorized_backtest.v67 import compact_execution as kernel
from research.vectorized_backtest.v67.supervision import SETTINGS


def replay(actions, signals, prices=None, *, pairs=None, chunks=False, candidates=2):
    n=len(actions);u=5;b=candidates
    slots=np.tile(np.arange(u,dtype=np.int32),(n,1))
    sig=np.zeros((n,b,u),np.int16);sig[:,:,0]=np.asarray(signals)[:,None]
    price=np.asarray(prices if prices is not None else np.linspace(10.,11.,n))
    book=tuple(np.tile(v[:,None],(1,u)) for v in
               (price,price,price,np.arange(n,dtype=float),np.full(n,100000.),np.full(n,100000.),np.ones(n,bool)))
    refs=np.zeros((n,b,u,5));refs[:,:,:,0]=100.;refs[:,:,:,1]=.01
    # A $100 limit yields ten initial shares; actual fills use the quote.
    refs[:,:,:,2]=1.;refs[:,:,:,3]=1000.;refs[:,:,:,4]=1.
    ratios=np.ones((n,b,u,3));owners=np.broadcast_to(np.arange(b)[None,:,None],(n,b,u)).copy()
    abstain=np.zeros((n,b,u),bool)
    action=np.zeros((n,u),np.int8);action[:,0]=actions
    pair=np.full((n,u),-1,np.int32);pair[:,0]=np.asarray(pairs if pairs is not None else np.ones(n),np.int32)
    deadline=np.full((n,u),-1,np.int32)
    for p in np.unique(pair[:,0]):
        exits=np.flatnonzero((pair[:,0]==p)&(action[:,0]==4))
        if len(exits):deadline[pair[:,0]==p,0]=exits[-1]
    # Read-only targets mirror the actual loader's Numba signature.
    for array in (action,pair,deadline):array.flags.writeable=False
    labels=(action,pair,deadline);state=kernel.state(b,u);old=baseline.state(b,u);score=kernel.supervision_state(b,u)
    records=[[] for _ in range(b)];episodes=[[] for _ in range(b)]
    old_records=[[] for _ in range(b)];old_episodes=[[] for _ in range(b)]
    for start,end in ([(i,i+1) for i in range(n)] if chunks else [(0,n)]):
        args=(start,slots[start:end],sig[start:end],ratios[start:end],refs[start:end],owners[start:end],abstain[start:end],tuple(v[start:end] for v in book))
        a,z=kernel.advance(*args,*state,1000.,200.,.005,0.,0.,n-1,labels,score)
        aa,zz=baseline.advance(*args,*old,1000.,200.,.005,0.,0.,n-1)
        for i in range(b):
            records[i].extend(a[i]);episodes[i].extend(z[i]);old_records[i].extend(aa[i]);old_episodes[i].extend(zz[i])
    for actual,prior in zip(state,old):np.testing.assert_array_equal(actual,prior)
    assert records==old_records and episodes==old_episodes
    return score,episodes,state


def test_aligned_profit_and_exact_execution_parity():
    score,episodes,_=replay([2,3,3,4,1],[1,0,0,2,0])
    assert score[0,0,6]==pytest.approx(episodes[0][0][0])
    assert score[0,0,7:10].tolist()==[0.,0.,0.]


def test_wait_entries_receive_no_positive_credit():
    score,episodes,_=replay([1,1,1,1,1],[1,0,0,2,0])
    assert episodes[0][0][0]>0 and score[0,0,6]==0
    assert score[0,0,7]==1 and score[0,0,8]==1


def test_unaligned_losses_still_count():
    score,episodes,_=replay([1]*5,[1,0,0,2,0],[11,10.8,10.5,10,10])
    assert episodes[0][0][0]<0
    assert score[0,0,6]==pytest.approx(episodes[0][0][0])


def test_missing_is_not_wait():
    score,_,_=replay([0]*5,[1,0,0,2,0])
    assert score[0,0,6:10].tolist()==[0.,0.,0.,0.]


def test_pair_specific_exit_and_late_exposure():
    score,_,_=replay([2,4,3,4,1],[1,0,0,2,0],pairs=[1,1,1,2,2])
    assert score[0,0,8]==1
    assert score[0,0,9]==pytest.approx(200.)  # ten $10 shares, two seconds late


def test_early_discretionary_exit():
    score,_,_=replay([2,3,3,4,1],[1,2,0,0,0])
    assert score[0,0,8]==1


def test_terminal_liquidation_exempt_from_exit_signal_penalty():
    score,_,_=replay([2,3,3,4,1],[1,0,0,0,0])
    assert score[0,0,8]==0 and score[0,0,9]>0


def test_unaligned_add_dilutes_profit():
    score,episodes,_=replay([2,3,3,4,1],[1,4,0,2,0])
    assert score[0,0,7]==1
    assert 0<score[0,0,6]<episodes[0][0][0]
    assert score[0,0,6]==pytest.approx(episodes[0][0][0]*100/(100+2*10.25))


def test_chunk_boundaries_preserve_scoring():
    args=([2,3,4,3,1,1],[1,4,0,0,2,0])
    a,_,_=replay(*args);b,_,_=replay(*args,chunks=True)
    np.testing.assert_array_equal(a,b)


def test_request_edge_deduplication():
    # Holding repeated ADD can fill additional orders, but is one signal episode.
    score,_,_=replay([2,3,3,3,4,1],[1,4,4,4,2,0])
    assert score[0,0,7]==1


def test_objective_rejects_unsupervised_metrics():
    from research.vectorized_backtest.v67.robust_score import ticker_component
    with pytest.raises(KeyError):ticker_component({'net_pnl':[100.]})


def test_manual_intervention_prohibited():
    from research.vectorized_backtest.v67.agent_intervention import check_policy,POLICY
    from research.vectorized_backtest.v67.review_window import enabled,SETTINGS as REVIEW
    check_policy({});assert not enabled({})
    with pytest.raises(ValueError):check_policy({'agent_intervention':POLICY})
    with pytest.raises(ValueError):enabled({'review_window':REVIEW})


def test_controller_handoff_retains_supervision_and_score():
    from research.vectorized_backtest.v67.controller_metrics import compact,SCALARS
    from research.vectorized_backtest.v67.full_common import aggregate,prepare_summary
    m={key:[0.] for key in SCALARS}
    m.update(net_pnl=[100.],closed_positions=[1.],winning_positions=[1.],terminal_valid=[True],
             episode_profits=[[100.]],episode_entry_clocks=[[0]],episode_exit_clocks=[[4]],
             episode_listings=[[0]],open_episode_listings=[[]],open_episode_realized=[[]],
             label_reward=[50.],label_penalty=[5.],label_entry_errors=[1.],
             label_ticker_reward=[[50.]],label_ticker_penalty=[[5.]])
    a=aggregate([m]);handed=compact(m);b=aggregate([handed])
    assert a['score'][0]==45.
    for key in a:np.testing.assert_array_equal(a[key],b[key])
    prepare_summary(handed)
    np.testing.assert_array_equal(aggregate([handed])['score'],a['score'])


def test_behavior_equivalence_includes_label_penalties():
    from research.vectorized_backtest.v67.behavior_selection import session_keys,SCALARS,LEDGERS
    m={key:[0.,0.] for key in SCALARS}
    m.update({key:[[],[]] for key in LEDGERS})
    m['label_penalty']=[0.,5.]
    assert session_keys(m)[0]!=session_keys(m)[1]
