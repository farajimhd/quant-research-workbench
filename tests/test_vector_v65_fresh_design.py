from collections import Counter
from dataclasses import replace
import numpy as np
import pytest
from research.vectorized_backtest.v65.fresh_design import FAMILIES,template,request_pattern,diverse_indices


@pytest.mark.parametrize('family',FAMILIES)
def test_templates_are_deterministic_small_ordinary_evolvable_genomes(family):
    from research.vectorized_backtest.v65.initial_population import validate_fresh
    from research.vectorized_backtest.v65.authority import dependency_certificate,BLOCKED
    from research.vectorized_backtest.v65.features import CATALOG
    a=template(np.random.default_rng(42),family);b=template(np.random.default_rng(42),family)
    assert a.payload()==b.payload()
    validate_fresh(a);dependency_certificate(a)
    assert all(len(n.nodes)<=2 for n in a.networks.values())
    assert all(s%len(CATALOG) not in BLOCKED for n in a.networks.values() for node in n.nodes for s in node.sources)
    assert not a.routing and not a.minimum_age


def test_diversity_selection_bounds_behavior_duplicates_and_template_share():
    rng=np.random.default_rng(7);patterns=rng.integers(0,256,size=(50,5,8),dtype=np.uint8)
    patterns[:10]=patterns[0]
    families=['ema_cross']*10+['macd_signal']*10+['random']*30
    keys=[str(i) for i in range(50)]
    selected,report=diverse_indices(patterns,families,keys,16,template_cap=6,family_cap=3)
    again,_=diverse_indices(patterns,families,keys,16,template_cap=6,family_cap=3)
    assert selected==again and len(selected)==len(set(selected))==16
    assert sum(families[i]!='random' for i in selected)<=6
    assert report['largest_request_pattern']<=2
    assert max(Counter(patterns[i].tobytes() for i in selected).values())<=2
    with pytest.raises(ValueError,match='distinct random'):
        diverse_indices(np.zeros_like(patterns),['random']*50,keys,16)


def probe():
    from research.vectorized_backtest.v65.genome import INPUTS
    from research.vectorized_backtest.v65.features import CATALOG
    from research.vectorized_backtest.v65.config import STATES
    shape=(2,8,2);rng=np.random.default_rng(8)
    values=rng.uniform(-.8,.8,(*shape,INPUTS)).astype(np.float32)
    return dict(sha256='synthetic-training-probe',inputs=values,known=np.ones((*shape,len(CATALOG)),bool),
        lag_absolute=-values[...,:len(CATALOG)],lag_known=np.ones((*shape,len(CATALOG)),bool),
        held_states=np.zeros((*shape,len(STATES)),np.float32),eligible=np.ones(shape,bool),
        quote_valid=np.ones(shape,bool),ask=np.full(shape,10.),ask_size=np.ones(shape),
        price_references={k:np.full(shape,v) for k,v in [('ask',10.),('bid',9.99),('close',10.),('volatility',.1)]})


def test_probe_screen_rejects_inactive_entries_without_profit_or_validation_inputs():
    p=probe();member=template(np.random.default_rng(3),'ema_cross')
    pattern,rates=request_pattern(member,p)
    assert pattern.shape==(5,4) and 0<rates[0]<1
    net=member.networks['entry'];disabled=replace(net,nodes=tuple(replace(n,op='tanh',bias=-8.) for n in net.nodes),threshold=.99)
    member=replace(member,networks=dict(member.networks,entry=disabled))
    with pytest.raises(ValueError,match='active and selective'):request_pattern(member,p)


def test_fresh_template_provenance_is_persisted_without_becoming_a_gene():
    from research.vectorized_backtest.v65.local_runner import CompactPedigree
    from research.vectorized_backtest.v65.lineage import program_key
    from research.vectorized_backtest.v65.templates import FAMILIES as ALL_FAMILIES
    member=template(np.random.default_rng(4),'ema_cross');key=program_key(member)
    object.__setattr__(member,'_fresh_family','ema_cross')
    assert program_key(member)==key
    pedigree=CompactPedigree();ids=pedigree.birth([member],2,[(-1,-1)],['fresh'])
    record=pedigree.records[int(ids[0])]
    assert record['template_ancestry']==1 and record['random_ancestry']==0
    assert record['family_contributions'][ALL_FAMILIES.index('ema_cross')]==1


def test_disk_admission_accounts_for_two_overlapping_generations_and_growth():
    from research.vectorized_backtest.v65.disk_budget import admission
    gib=2**30
    assert admission(128*gib)['admitted']
    assert not admission(127*gib)['admitted']
    assert admission(200*gib,40*gib)['required_bytes']==184*gib
    assert not admission(180*gib,40*gib)['admitted']


def test_selective_priors_are_calibration_only_and_latent_heads_still_have_market_dependencies():
    from research.vectorized_backtest.v65.calibration import _calibrate,observe
    from research.vectorized_backtest.v65.genome import sample
    from research.vectorized_backtest.v65.authority import dependency_certificate
    rng=np.random.default_rng(101);p=probe()
    policy=template(rng,'ema_cross').policy
    members=[replace(m,policy=policy) for m in sample(rng,16)]
    members,report=_calibrate(rng,members,p,selective=True)
    entries=[r for r in report['records'] if r['stage']=='entry']
    assert all(.001<=r['target_rate']<=.10 for r in entries)
    latent=[r for r in report['records'] if r['initially_inactive']]
    assert latent
    for r in latent:
        member=members[r['candidate']];dependency_certificate(member)
        net=member.networks[r['stage']];values,known,_,_=observe(net,p,r['stage'])
        assert not (known&(values>net.threshold)).any()
