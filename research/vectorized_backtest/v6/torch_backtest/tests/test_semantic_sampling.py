import numpy as np
from research.vectorized_backtest.v6.torch_backtest.evolution import sample,mutate,semantic_family,conditional_policy,reject_degenerate
from research.vectorized_backtest.v6.torch_backtest.genome import StrategySpace,NAMES
from research.vectorized_backtest.v6.torch_backtest.program import Node,Op
from research.vectorized_backtest.v6.torch_backtest.feature_bank import CATALOG


def test_seeded_population_contains_no_absolute_price_or_structural_identity():
    rng=np.random.default_rng(89);space=StrategySpace();population=sample(rng,space,64)
    for _ in range(4):population=[mutate(rng,p,space) for p in population]
    for member in population:
        for clauses in member.clauses.values():
            for nodes,_ in clauses:
                assert not reject_degenerate(nodes)
                assert all(CATALOG[n.feature].unit!='log_price' for n in nodes if n.op==Op.FEATURE)
        space.validate(np.array([member.policy]))
    assert semantic_family(CATALOG[27])!=semantic_family(CATALOG[26])


def test_conditional_genes_freeze_and_initialize_on_activation():
    space=StrategySpace();old=space.default.copy();old[7]=0
    old=conditional_policy(old,space)
    j=space.policy_start+NAMES.index('adaptive_multiplier')
    assert old[j]==space.default[j]
    new=old.copy();new[7]=1
    a=conditional_policy(new.copy(),space,previous=old,rng=np.random.default_rng(3))
    b=conditional_policy(new.copy(),space,previous=old,rng=np.random.default_rng(3))
    assert a[j]!=old[j] and np.array_equal(a,b)
    assert a[space.policy_start+NAMES.index('trail_up_fraction')]==space.default[space.policy_start+NAMES.index('trail_up_fraction')]
