import numpy as np
import pytest
from research.vectorized_backtest.v65.founder_pool import combination,validate
from research.vectorized_backtest.v65.fresh_design import FAMILIES
from research.vectorized_backtest.v65.founder_selection import select


def test_no_positive_sessions_cannot_earn_profit_dispersion_credit():
    from research.vectorized_backtest.v65.founder_selection import profit_concentration
    value=profit_concentration(np.array([[-1.,-2.],[0.,0.],[2.,0.],[1.,1.]]))
    np.testing.assert_array_equal(value,[1.,1.,1.,.5])


def test_probe_rarity_prefers_coverage_but_does_not_hard_exclude_patterns():
    from collections import Counter
    from research.vectorized_backtest.v65.founder_pool import rarity_indices
    patterns=['common']*8+['new-a','new-b']
    assert rarity_indices(patterns,Counter(common=10),2)==[8,9]
    assert len(rarity_indices(patterns,Counter(common=10),10))==10
    assert rarity_indices(['a','a','b','b'],Counter(),4)==[0,2,1,3]


@pytest.mark.parametrize('family',FAMILIES)
@pytest.mark.parametrize('filter_index',range(4))
def test_combinations_use_valid_three_node_causal_graphs(family,filter_index):
    a=combination(np.random.default_rng(17),family,filter_index)
    b=combination(np.random.default_rng(17),family,filter_index)
    validate(a)
    assert a.payload()==b.payload()
    assert len(a.networks['entry'].nodes)==3


def evidence(n=48):
    rng=np.random.default_rng(3);ordered=[]
    for day in range(5):
        ordered.append(dict(net_pnl=rng.normal(10,30,n),closed_positions=np.full(n,5),
            episode_listings=[[i%7] for i in range(n)],episode_entry_clocks=[[i*300] for i in range(n)],
            episode_exit_clocks=[[i*300+60] for i in range(n)]))
    panel=dict(score=rng.normal(size=n),eligible=np.ones(n,bool))
    return ordered,panel


def test_selection_is_deterministic_unique_and_uses_supported_training_only():
    ordered,panel=evidence();panel['eligible'][0]=False;panel['score'][0]=1e9
    chosen,report=select(ordered,panel,8)
    again,_=select(ordered,panel,8)
    assert chosen==again and len(set(chosen))==8 and 0 not in chosen
    assert report['quality_selected']==6 and report['exploration_selected']==2
    assert not report['validation_used'] and not report['reproduction_performed']
    assert report['largest_selected_group']<=report['group_cap']


def test_insufficient_support_fails_without_silent_inactive_filler():
    ordered,panel=evidence()
    for m in ordered:m['closed_positions'][:]=0
    with pytest.raises(ValueError,match='supported founders'):select(ordered,panel,8)


def test_identical_behavior_capacity_is_explicit_not_hidden():
    ordered,panel=evidence()
    for m in ordered:
        m['net_pnl'][:]=1
        for key in ('episode_listings','episode_entry_clocks','episode_exit_clocks'):
            m[key]=[m[key][0]]*48
    chosen,report=select(ordered,panel,8)
    assert report['behavior_groups']==1 and report['group_cap']==8
    assert len(set(chosen))==8
