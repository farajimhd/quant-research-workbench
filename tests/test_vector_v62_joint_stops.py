"""Creation contracts, immutable restore, and real CPU/CUDA order transitions."""
from dataclasses import replace
import os
import numpy as np
import pytest
import torch
from research.vectorized_backtest.v62.authority import (
    ENTRY_STOP_RULE,BOUNDED_SWING,construct,entry_stop,feasible_policy,policy_values)
from research.vectorized_backtest.v62.policy import Policy


def test_joint_envelope_extremes_and_missing_history():
    ask=np.geomspace(.00001,100000,41)[:,None]
    # Invalid, absent, above quote, below quote but above limit, valid swings.
    swing=ask*np.array([np.nan,np.inf,-np.inf,-1.,0.,2.,1.,.995,.5,1e-10])
    for sf in (.0001,.03,.9999):
        for offset in (-.1,-.01,0.,.1):
            p=feasible_policy(Policy(stop_fraction=sf,buy_limit_offset=offset,swing_left=3,swing_right=30)).validate()
            s=entry_stop(ask,swing,p.stop_fraction,p.buy_limit_offset,policy_values(p)[8],xp=np)
            assert np.isfinite(s).all() and (s>0).all()
            assert (s<ask).all() and (s<ask*(1+p.buy_limit_offset)).all()
            assert feasible_policy(p)==p
            np.testing.assert_array_equal(s[:,-2:],swing[:,-2:])
            torch.testing.assert_close(entry_stop(torch.tensor(ask),torch.tensor(swing),p.stop_fraction,p.buy_limit_offset,torch.tensor(policy_values(p)[8]),xp=torch),torch.tensor(s),rtol=0,atol=0)


def test_legacy_restore_is_unchanged_and_conversion_is_explicit():
    from research.vectorized_backtest.v62.genome import sample,Individual
    from research.vectorized_backtest.v62.lineage import program_key
    from research.vectorized_backtest.v62.identity_cache import identity_scope
    m=sample(np.random.default_rng(12),1)[0]
    old=replace(m,policy=Policy(swing_left=3,swing_right=30,stop_fraction=.001,buy_limit_offset=-.01))
    payload=old.payload()
    assert 'entry_stop_rule' not in payload['policy']
    restored=Individual.restore(payload)
    assert restored.payload()==payload
    with identity_scope():
        old_key=program_key(old)
        assert program_key(restored)==old_key
        new=construct(restored)
        assert program_key(new)!=old_key
    assert payload==old.payload()
    with pytest.raises(ValueError,match='Unknown entry-stop'):replace(new.policy,entry_stop_rule='other').validate()
    a=np.array([10.,10.]);sw=np.array([np.nan,11.])
    np.testing.assert_array_equal(entry_stop(a,sw,.001,-.01,3,xp=np),sw)


def test_every_creation_route_enforces_joint_stop_contract():
    from research.vectorized_backtest.v62.genome import sample,mutate,crossover
    from research.vectorized_backtest.v62.initial_design import policies
    from research.vectorized_backtest.v62.strategy_mix import cross,leaves,route
    from research.vectorized_backtest.v62.whole_network import cross as whole
    from research.vectorized_backtest.v62.population_builder import build
    rng=np.random.default_rng(414)
    parents=sample(rng,12)
    legacy=[replace(m,policy=Policy(swing_left=3,swing_right=30,stop_fraction=.001,buy_limit_offset=-.01)) for m in parents]
    children=list(parents)+policies(rng,parents)+[mutate(rng,m) for m in legacy]
    masked,report=build(12,lambda r,i,a:crossover(r,legacy[i],legacy[(i+1)%12]),lambda r,i,a:sample(r,1)[0],seed=98)
    assert len(masked)==report['accepted']==12
    children+=masked
    for mode in ('entry_routing','entry_management_exchange','subgraph_exchange','reciprocal_veto'):
        children.append(cross(legacy[0],legacy[1],mode)[0])
    for mode in ('union','intersection','xor','xnor'):
        children.append(whole(legacy[0],legacy[1],mode)[0])
    # Routing mutation must upgrade unedited branches too.
    old_route=replace(legacy[0],routing=tuple(legacy[:2]))
    children.extend([mutate(rng,old_route),route(*legacy[:2])])
    for m in children:
        for leaf in leaves(m):
            p=leaf.policy.validate()
            assert p.entry_stop_rule==ENTRY_STOP_RULE
            assert p.stop_fraction+p.buy_limit_offset>=1e-6-1e-12


def transition_args(device='cpu'):
    p=feasible_policy(Policy(swing_left=3,swing_right=30,stop_fraction=.03))
    return dict(position=torch.zeros((1,1,5,9),dtype=torch.float64,device=device),
        orders=torch.zeros((1,1,5,17),dtype=torch.float64,device=device),
        signals=torch.ones((1,1,5),dtype=torch.int16,device=device),
        ratios=torch.ones((1,1,5,3),dtype=torch.float64,device=device),
        policy=torch.tensor(policy_values(p),dtype=torch.float64,device=device)[None,None,None,:],
        mark=torch.full((1,1,5),10.,dtype=torch.float64,device=device),
        previous=torch.full((1,1,5),10.,dtype=torch.float64,device=device),
        membership=torch.ones((1,1,5),dtype=torch.bool,device=device),
        observed=torch.ones((1,1,5),dtype=torch.bool,device=device),
        swing=torch.tensor([np.nan,0.,11.,10.,9.],dtype=torch.float64,device=device)[None,None,:],
        bid=torch.full((1,1,5),9.99,dtype=torch.float64,device=device),
        ask=torch.full((1,1,5),10.,dtype=torch.float64,device=device),
        quote_valid=torch.ones((1,1,5),dtype=torch.bool,device=device),
        capacity=torch.full((1,1,5,2),100.,dtype=torch.float64,device=device),
        clock=torch.tensor(1.,dtype=torch.float64,device=device),
        terminal=torch.tensor(False,device=device),entry_dollars=1000.,add_dollars=200.,
        fee_per_share=0.,minimum_order_fee=0.,cost=0.,minimum_order_dollars=0.)


def test_real_order_transition_uses_candidate_rule_and_preserves_legacy():
    from research.vectorized_backtest.v62.orders import transition
    args=transition_args();result=transition(**args);position=result[0]
    assert (position[...,0]==100.).all()
    assert (position[...,2]>0).all() and (position[...,2]<position[...,1]).all()
    np.testing.assert_array_equal(position[0,0,:,2], [9.7,9.7,9.99999,9.99999,9.])
    args=transition_args();args['policy'][...,8]=3
    old=transition(**args)[0]
    assert (old[0,0,:4,0]==0).all() and old[0,0,4,0]==100.


@pytest.mark.skipif(os.environ.get('V60_TEST_CUDA')!='1',reason='opt-in workstation GPU')
def test_real_cuda_order_transition_exact_parity():
    from research.vectorized_backtest.v62.orders import transition
    from research.vectorized_backtest.v62.order_transition import transition as cuda
    # Both modes, including pending unfilled entries, must agree exactly.
    for mode in (3,BOUNDED_SWING+3):
        args=transition_args('cuda');args['policy'][...,8]=mode
        expected=transition(**args)
        actual=cuda(args['position'],args['orders'],args['signals'],args['ratios'],args['policy'],
                    args['previous'],args['membership'],args['swing'],args['bid'],args['ask'],
                    args['quote_valid'],args['capacity'],args['clock'],args['terminal'],
                    args['entry_dollars'],args['add_dollars'],args['fee_per_share'],
                    args['minimum_order_fee'],args['cost'],args['minimum_order_dollars'])
        for a,b in zip(actual,expected):torch.testing.assert_close(a,b,rtol=0,atol=0,equal_nan=True)
