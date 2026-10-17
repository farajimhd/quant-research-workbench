"""Executable ownership, typed operators and sparse training behavior contracts."""
from dataclasses import replace
import numpy as np
import pytest
import torch
from research.vectorized_backtest.v27.genome import sample,Individual
from research.vectorized_backtest.v27.composition import routed,cross,mutation,OPERATORS
from research.vectorized_backtest.v27.lineage import program_key
from research.vectorized_backtest.v27.evaluator import replay_cohort,Execution
from research.vectorized_backtest.v7.qualify import synthetic


def members():
    return [replace(m,policy=replace(m.policy,swing_left=0,swing_right=0,stop_fraction=.1,target_fraction=.9,cooldown=1,maximum_adds=0)) for m in sample(np.random.default_rng(12),2)]


def run(member,left,right=None,condition=None,execution=Execution()):
    l=torch.tensor(left,dtype=torch.int32)[None,:,None]
    if right is None:g=l.to(torch.uint8)
    else:
        r=torch.tensor(right,dtype=torch.int32)[None,:,None]
        c=torch.tensor(condition if condition is not None else [1]*6,dtype=torch.int32)[None,:,None]
        g=l|(r<<5)|(c<<10)
    return replay_cohort([synthetic('cpu')],[member],[g],execution=execution,trace=True)[0]


def test_priority_same_leaf_preserves_exact_financial_results_and_position_clocks():
    a,b=members();g=[1,0,0,0,0,0]
    leaf=run(a,g);route=run(routed('priority',a,a),g,g)
    for k in leaf:
        if isinstance(leaf[k],torch.Tensor):assert torch.equal(leaf[k],route[k]),k
        else:assert leaf[k]==route[k],k


def test_conditional_ownership_is_sticky_and_unknown_condition_does_not_enter():
    a,b=members();child=routed('conditional',a,b,a.rules['entry'])
    # A owns the position. Later routing changes and B's exit cannot seize it.
    z=run(child,[1,0,0,0,0,0],[1,2,2,2,2,0],[1,2,2,2,2,2])
    assert z['episode_entry_clocks']==[[1]] and z['episode_exit_clocks']==[[5]]
    z=run(child,[1,0,0,0,0,0],[1,0,0,0,0,0],[0]*6)
    assert z['episode_profits']==[[]]


def test_priority_falls_back_when_first_branch_fails_risk_eligibility():
    a,b=members();a=replace(a,policy=replace(a.policy,stop_fraction=.2));b=replace(b,policy=replace(b.policy,stop_fraction=.01))
    execution=Execution(maximum_position_risk=50)
    g=[1,0,0,0,0,0]
    expected=run(b,g,execution=execution);actual=run(routed('priority',a,b),g,g,execution=execution)
    assert actual['episode_profits']==expected['episode_profits']
    assert torch.equal(actual['net_pnl'],expected['net_pnl'])


@pytest.mark.parametrize('operator',OPERATORS)
def test_compositions_roundtrip_and_mutations_remain_bounded(operator):
    rng=np.random.default_rng(83);a,b=members()
    # Rejections are explicit for over-budget or semantically invalid expression combinations.
    for _ in range(32):
        try:child,op=cross(rng,a,b,operator);break
        except ValueError:a,b=sample(rng,2)
    else:pytest.fail('No valid representative composition')
    assert program_key(child)==program_key(Individual.restore(child.payload()))
    changed,edits=mutation(rng,child,2,True)
    changed.validate();assert edits
    if changed.routing:assert changed.routing['left'].routing is changed.routing['right'].routing is None


def test_behavior_overlap_matches_nested_intervals_and_has_bounded_pair_cache():
    from research.vectorized_backtest.v27.behavior import BehaviorIndex
    m=dict(net_pnl=[1,2,3],episode_listings=[[0],[0],[1]],episode_entry_clocks=[[10],[20],[10]],episode_exit_clocks=[[100],[50],[100]])
    index=BehaviorIndex([m]);p=index.similarity(0,1)
    assert p['holding']==pytest.approx(1/3) and p['coverage_right']==1
    assert index.similarity(0,2)['score']==0
    assert index.similarity(0,1)==p and index.exact_queries==2
    assert not BehaviorIndex([dict(net_pnl=[1])]).available


@pytest.mark.parametrize('device',['cpu','cuda'])
def test_packed_signal_diagnostics_match_explicit_branch_union(device):
    if device=='cuda' and not torch.cuda.is_available():pytest.skip('CUDA required')
    from research.vectorized_backtest.v27.signal_counts import count
    g=torch.randint(0,4096,(3,9,13),dtype=torch.int16,device=device)
    plain=((g&31)|((g>>5)&31)).to(torch.uint8)
    assert torch.equal(count(g),count(plain))
