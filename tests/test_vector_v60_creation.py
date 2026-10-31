from dataclasses import replace
import numpy as np
import pytest


def test_joint_policy_projection_and_restore_rejection():
    from research.vectorized_backtest.v60.authority import feasible_policy
    from research.vectorized_backtest.v60.policy import Policy
    bad=Policy(stop_fraction=.001,buy_limit_offset=-.01)
    with pytest.raises(ValueError,match='joint'):bad.validate()
    p=feasible_policy(bad).validate()
    for ask in (.01,1.,100.,10000.):
        assert 0<ask*(1-p.stop_fraction)<ask*(1+p.buy_limit_offset)
    assert feasible_policy(p)==p


def test_joint_sampling_and_mutation():
    from research.vectorized_backtest.v60.genome import sample,mutate,crossover
    from research.vectorized_backtest.v60.initial_design import policies
    rng=np.random.default_rng(104)
    members=policies(rng,sample(rng,24,stratified=True))
    for i,m in enumerate(members):
        m.validate();mutate(rng,m).validate()
    from research.vectorized_backtest.v60.population_builder import build
    children,report=build(24,lambda r,i,a:crossover(r,members[i],members[(i+1)%24]),
        lambda r,i,a:sample(r,1)[0],seed=104)
    assert len(children)==report['accepted']==24
    for child in children:child.validate()


def test_shared_packing_keeps_exact_rows_and_reduces_storage():
    from research.vectorized_backtest.v60.genome import sample
    from research.vectorized_backtest.v60.network_pack import pack_networks,shared_storage
    from research.vectorized_backtest.v60.entry_sharing import action_representatives
    m=sample(np.random.default_rng(11),1)[0];members=[m,m,m]
    original=pack_networks(members);shared=shared_storage(original,action_representatives(members))
    assert shared['total_nodes']*3==original['total_nodes']
    for net,count in enumerate(original['values'][3]):
        a=int(original['values'][4][net]);b=int(shared['values'][4][net])
        for i in (0,1,2):np.testing.assert_array_equal(original['values'][i][a:a+count],shared['values'][i][b:b+count])
