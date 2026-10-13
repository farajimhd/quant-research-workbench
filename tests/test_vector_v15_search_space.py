"""Reachability, parameter evolution and initialization isolation regressions."""
from dataclasses import asdict
import numpy as np
import torch
import pytest
from research.vectorized_backtest.v15.features import CATALOG
from research.vectorized_backtest.v15.genome import sample,condition,mutate,crossover,effective_rules,STAGES
from research.vectorized_backtest.v15.search_space import (GROUPS,feature_index,semantic,threshold,
    family,PRICE_GROUPS,MUTATION_GROUPS,comparison_feature,mutation_feature)
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
    flattened=[i for group in GROUPS for i in group]
    assert sorted(flattened)==list(range(len(CATALOG)))
    rng=np.random.default_rng(7);draws=np.array([feature_index(rng) for _ in range(60000)])
    mapping={i:g for g,group in enumerate(GROUPS) for i in group}
    counts=np.bincount([mapping[i] for i in draws],minlength=len(GROUPS))
    expected=len(draws)/len(GROUPS)
    assert np.max(np.abs(counts-expected))<6*np.sqrt(expected)
    for group in GROUPS:
        assert len({family(CATALOG[i]) for i in group})==1


def test_average_move_thresholds_match_persisted_shift_and_price_peers():
    rng=np.random.default_rng(41)
    index=next(i for i,f in enumerate(CATALOG) if 'average_move.' in f.name and f.unit=='ratio')
    values=np.array([threshold(rng,index) for _ in range(1000)])
    assert np.all(values>-1) and np.all(values<.5) and np.any(values<-.9999)
    raw=next(i for i,f in enumerate(CATALOG) if 'average_move.' in f.name and f.unit=='price')
    assert all(semantic(CATALOG[i])=='magnitude' for group in PRICE_GROUPS[raw] for i in group)


def test_every_compatibility_table_preserves_constraints_and_excludes_self():
    for index,f in enumerate(CATALOG):
        expected={i for i,g in enumerate(CATALOG) if i!=index and g.unit==f.unit
                  and semantic(g)==semantic(f) and (g.lower,g.upper)==(f.lower,f.upper)}
        actual={i for group in MUTATION_GROUPS[index] for i in group}
        assert actual==expected
        if not expected:assert mutation_feature(np.random.default_rng(1),index)==index
        if f.unit=='price':
            expected={i for i,g in enumerate(CATALOG) if i!=index and g.unit=='price' and semantic(g)==semantic(f)}
            assert {i for group in PRICE_GROUPS[index] for i in group}==expected


@pytest.mark.parametrize('route',['comparison','mutation'])
def test_conditional_draws_are_equal_across_eligible_families(route):
    index=next(i for i,f in enumerate(CATALOG) if f.name=='history.recent_high.1')
    groups=PRICE_GROUPS[index] if route=='comparison' else MUTATION_GROUPS[index]
    draw=comparison_feature if route=='comparison' else mutation_feature
    lookup={i:g for g,group in enumerate(groups) for i in group}
    rng=np.random.default_rng(23)
    counts=np.bincount([lookup[draw(rng,index)] for _ in range(30000)],minlength=len(groups))
    expected=30000/len(groups)
    assert max(np.abs(counts-expected))<6*np.sqrt(expected)


def test_genome_uses_balanced_helpers(monkeypatch):
    import research.vectorized_backtest.v15.search_space as space
    calls={'comparison':0,'mutation':0}
    old_comparison=space.comparison_feature;old_mutation=space.mutation_feature
    def comparison(rng,index):calls['comparison']+=1;return old_comparison(rng,index)
    def mutation(rng,index):calls['mutation']+=1;return old_mutation(rng,index)
    monkeypatch.setattr(space,'comparison_feature',comparison);monkeypatch.setattr(space,'mutation_feature',mutation)
    rng=np.random.default_rng(7)
    index=next(i for i,f in enumerate(CATALOG) if f.name=='history.recent_high.1')
    condition(rng,index);assert calls['comparison']==1
    parent=sample(rng,1)[0]
    for _ in range(100):mutate(rng,parent)
    assert calls['mutation']>0


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


@pytest.mark.skipif(not torch.cuda.is_available(),reason='CUDA qualification runs on workstation')
@pytest.mark.parametrize('shape',[(129,len(CATALOG)),(2,257,len(CATALOG))])
def test_fused_cuda_matches_original_with_missing_and_boundary_values(shape):
    from research.vectorized_backtest.v15.fused_program import FusedPrograms
    rng=np.random.default_rng(89)
    members=sample(rng,128)
    x=torch.from_numpy(rng.normal(size=shape).astype(np.float32)).cuda()
    valid=torch.from_numpy(rng.random(size=shape)>.15).cuda()
    x.reshape(-1)[::17]=0.;x.reshape(-1)[::29]=float('inf');x.reshape(-1)[::31]=float('nan')
    for stage in STAGES:
        programs=[effective_rules(m)[stage] for m in members]
        old=TorchPrograms(programs,CATALOG,'cuda');new=FusedPrograms(programs,CATALOG,'cuda')
        expected,known=old(x,valid);actual,mask=new(x,valid)
        torch.testing.assert_close(actual,expected,rtol=0,atol=0)
        torch.testing.assert_close(mask,known,rtol=0,atol=0)
