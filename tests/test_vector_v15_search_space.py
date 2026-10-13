"""Reachability, parameter evolution and initialization isolation regressions."""
from dataclasses import asdict
import numpy as np
import torch
from research.vectorized_backtest.v15.features import CATALOG
from research.vectorized_backtest.v15.genome import sample,condition,mutate,crossover,effective_rules,STAGES
from research.vectorized_backtest.v15.search_space import GROUPS,feature_index,semantic,threshold
from research.vectorized_backtest.v6.torch_backtest.program import Op,TorchPrograms


def test_boolean_atoms_are_nonconstant_and_missing_evidence_stays_false():
    rng=np.random.default_rng(13)
    for i,f in enumerate(CATALOG):
        if f.unit!='bool':continue
        for _ in range(8):
            rule=condition(rng,i);engine=TorchPrograms([rule],CATALOG)
            x=torch.zeros((3,len(CATALOG)));x[1,i]=1
            known=torch.ones_like(x,dtype=torch.bool);known[2,i]=False
            values,valid=engine(x,known)
            assert set(values[0,:2].tolist())=={0.,1.} and not valid[0,2]
            assert all(n.op in (Op.FEATURE,Op.NOT) for n in rule.nodes)


def test_family_weights_and_all_feature_reachability():
    flattened=[i for group in GROUPS for family in group for i in family]
    assert sorted(flattened)==list(range(len(CATALOG)))
    rng=np.random.default_rng(7);draws=np.array([feature_index(rng) for _ in range(60000)])
    assert len(np.unique(draws))==len(CATALOG)
    for group in GROUPS:
        indices=[i for family in group for i in family]
        assert .24<np.isin(draws,indices).mean()<.26


def test_average_move_thresholds_match_persisted_shift_and_price_peers():
    from research.vectorized_backtest.v15.genome import PRICE_PEERS
    rng=np.random.default_rng(41)
    index=next(i for i,f in enumerate(CATALOG) if 'average_move.' in f.name and f.unit=='ratio')
    values=np.array([threshold(rng,index) for _ in range(1000)])
    assert np.all(values>-1) and np.all(values<.5) and np.any(values<-.9999)
    assert all(semantic(CATALOG[i])=='magnitude' for i in PRICE_PEERS['magnitude'])
    assert not set(PRICE_PEERS['price'])&set(PRICE_PEERS['magnitude'])


def test_all_stages_operators_and_policy_genes_are_sampled_and_mutable():
    rng=np.random.default_rng(18);population=sample(rng,2048)
    ops=set();widths={s:set() for s in STAGES};policies={k:set() for k in asdict(population[0].policy)}
    ages={s:set() for s in STAGES if s!='entry'}
    for member in population:
        member.validate()
        for s,rule in effective_rules(member).items():
            ops.update(n.op for n in rule.nodes);widths[s].add(sum(n.op in (Op.AND,Op.OR) for n in rule.nodes))
        for k,v in asdict(member.policy).items():policies[k].add(v)
        for s in ages:ages[s].add(member.minimum_age.get(s,0))
        assert member.minimum_age.get('entry',0)==0  # Ignored while flat; not a fake gene.
    assert ops==set(Op)
    assert all({0,1,2}<=counts for counts in widths.values())
    assert all(len(values)>1 for values in policies.values())
    assert all(0 in values and max(values)>250 for values in ages.values())
    parent=population[0];changed=set();changed_age=set()
    for _ in range(1200):
        child=mutate(rng,parent);child.validate()
        changed.update(k for k,v in asdict(child.policy).items() if v!=getattr(parent.policy,k))
        changed_age.update(s for s in ages if child.minimum_age.get(s,0)!=parent.minimum_age.get(s,0))
    assert changed==set(policies) and changed_age==set(ages)


def test_crossover_recombines_individual_policy_parameters():
    rng=np.random.default_rng(99);a,b=sample(rng,2)
    independent=[k for k in asdict(a.policy) if not k.startswith('swing_')]
    children=[crossover(rng,a,b) for _ in range(50)]
    assert any(any(getattr(c.policy,k)==getattr(a.policy,k) for k in independent)
               and any(getattr(c.policy,k)==getattr(b.policy,k) for k in independent) for c in children)
    for c in children:
        c.validate()
        assert all(getattr(c.policy,k) in (getattr(a.policy,k),getattr(b.policy,k)) for k in independent)
