from dataclasses import replace
import numpy as np
import pytest


def test_joint_policy_projection_and_restore_rejection():
    from research.vectorized_backtest.v62.authority import feasible_policy
    from research.vectorized_backtest.v62.policy import Policy
    bad=Policy(stop_fraction=.001,buy_limit_offset=-.01)
    with pytest.raises(ValueError,match='joint'):bad.validate()
    p=feasible_policy(bad).validate()
    for ask in (.01,1.,100.,10000.):
        assert 0<ask*(1-p.stop_fraction)<ask*(1+p.buy_limit_offset)
    assert feasible_policy(p)==p


def test_joint_sampling_and_mutation():
    from research.vectorized_backtest.v62.genome import sample,mutate,crossover
    from research.vectorized_backtest.v62.initial_design import policies
    rng=np.random.default_rng(104)
    members=policies(rng,sample(rng,24,stratified=True))
    for i,m in enumerate(members):
        m.validate();mutate(rng,m).validate()
    from research.vectorized_backtest.v62.population_builder import build
    children,report=build(24,lambda r,i,a:crossover(r,members[i],members[(i+1)%24]),
        lambda r,i,a:sample(r,1)[0],seed=104)
    assert len(children)==report['accepted']==24
    for child in children:child.validate()


def test_shared_packing_keeps_exact_rows_and_reduces_storage():
    from research.vectorized_backtest.v62.genome import sample
    from research.vectorized_backtest.v62.network_pack import pack_networks,shared_storage
    from research.vectorized_backtest.v62.entry_sharing import action_representatives
    m=sample(np.random.default_rng(11),1)[0];members=[m,m,m]
    original=pack_networks(members);shared=shared_storage(original,action_representatives(members))
    assert shared['total_nodes']*3==original['total_nodes']
    for net,count in enumerate(original['values'][3]):
        a=int(original['values'][4][net]);b=int(shared['values'][4][net])
        for i in (0,1,2):np.testing.assert_array_equal(original['values'][i][a:a+count],shared['values'][i][b:b+count])


def test_recovery_template_fires_on_fast_ema_reclaim():
    from research.vectorized_backtest.v62.templates import build,INDEX
    from research.vectorized_backtest.v62.features import CATALOG
    from research.vectorized_backtest.v62.calibration import observe
    c=len(CATALOG);x=np.zeros((1,3,1,3*c+18),np.float32);known=np.ones((1,3,1,c),bool)
    # Fast EMA remains above slow EMA; price moves from below to above fast.
    x[0,:,0,INDEX['indicator.ema.7.relative']]=np.tanh([.01,.005,-.002])
    x[0,:,0,INDEX['indicator.ema.26.relative']]=np.tanh([-.02,-.025,-.03])
    x[...,2*c:3*c]=1
    p=dict(inputs=x,known=known,held_states=np.zeros((1,3,1,18),np.float32))
    v,k,_,_=observe(build('trend_pullback',1).networks['entry'],p)
    assert k[0,2,0] and v[0,2,0]>0
    assert not (v[0,:2,0]>0).any()


def test_parallel_calibration_is_repeatable_and_entry_is_active():
    from research.vectorized_backtest.v62.genome import sample,MARKET_INPUTS
    from research.vectorized_backtest.v62.features import CATALOG
    from research.vectorized_backtest.v62.calibration import calibrate,observe
    c=len(CATALOG);r=np.random.default_rng(55)
    x=r.uniform(-1,1,(2,8,3,MARKET_INPUTS+18)).astype(np.float32)
    known=np.ones((2,8,3,c),bool);x[...,2*c:3*c]=1
    p=dict(inputs=x,known=known,eligible=np.ones((2,8,3),bool),held_states=np.zeros((2,8,3,18),np.float32),sha256='synthetic')
    members=sample(r,4)
    a,report=calibrate(np.random.default_rng(8),members,p)
    b,_=calibrate(np.random.default_rng(8),members,p)
    assert [m.payload() for m in a]==[m.payload() for m in b]
    assert report['unresolved_constant']==0
    for m in a:
        v,k,_,_=observe(m.networks['entry'],p)
        assert (k&(v>m.networks['entry'].threshold)).any()


def test_complete_fresh_founders_and_replacement_boundary():
    from research.vectorized_backtest.v62.initial_population import initialize,immigrant,validate_fresh
    from research.vectorized_backtest.v62.genome import MARKET_INPUTS,random_network
    from research.vectorized_backtest.v62.features import CATALOG
    from research.vectorized_backtest.v62.population_builder import build,canonical_key
    c=len(CATALOG);r=np.random.default_rng(55)
    x=r.uniform(-1,1,(2,8,3,MARKET_INPUTS+18)).astype(np.float32)
    x[...,2*c:3*c]=1
    probe=dict(inputs=x,known=np.ones((2,8,3,c),bool),eligible=np.ones((2,8,3),bool),
        held_states=np.zeros((2,8,3,18),np.float32),sha256='synthetic')
    members,report=initialize(np.random.default_rng(2021),6144,probe=probe)
    assert len(members)==len({canonical_key(m) for m in members})==6144
    assert report['seed_count']==0 and report['random_count']==6144
    assert report['initial_families']==['simple']*6144
    assert report['founder_nodes']['min']>=5 and report['founder_nodes']['max']<=10
    for m in members:validate_fresh(m)
    large=replace(members[0],networks=dict(members[0].networks,entry=random_network(r,'entry',4)))
    large.validate()  # The restriction applies to fresh construction, not evolution.
    with pytest.raises(ValueError,match='1-2 nodes'):validate_fresh(large)
    children,counts=build(4,lambda *args:validate_fresh(large),
        lambda rng,*args:immigrant(rng,probe),seed=19,attempts=1)
    assert counts['fallback_fresh']==4 and len(children)==4
    for child in children:validate_fresh(child)
    from research.vectorized_backtest.v62.reproduction_prefetch import FreshPrefetch
    lane=FreshPrefetch(np.random.default_rng(17),2,3,probe)
    try:
        for i in range(3):validate_fresh(lane.get(lane.root,2,i,0))
    finally:lane.close()


def test_capacity_and_execution_are_unchanged():
    from research.vectorized_backtest.v61 import config as old
    from research.vectorized_backtest.v62 import config as new
    assert new.INITIAL_SIZES==(1,2)
    for name in ('MAX_NODES','MAX_STRATEGY_NODES','MAX_DEPTH','FAN_IN'):
        assert getattr(new,name)==getattr(old,name)
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]/'research'/'vectorized_backtest'
    # Entry-stop construction is now versioned in V62; score/execution sizing
    # and the surrounding replay machinery retain the inherited contract.
    for name in ('robust_score.py','order_replay.py','network.py','growth.py','lineage.py'):
        assert (root/'v62'/name).read_text()==(root/'v61'/name).read_text().replace('v61','v62').replace('V61','V62')


def test_founder_cache_rejects_oversized_fresh_candidate(tmp_path):
    import json
    from research.vectorized_backtest.v62.full_seed import global_seed
    from research.vectorized_backtest.v62.genome import sample,random_network
    rng=np.random.default_rng(4);m=sample(rng,1)[0]
    large=replace(m,networks=dict(m.networks,entry=random_network(rng,'entry',3)))
    state=dict(source_sha256='test',validation_opened=False,population=[large.payload()]*6144,sources=[])
    (tmp_path/'fresh-founders.json').write_text(json.dumps(state))
    with pytest.raises(ValueError,match='1-2 nodes'):
        global_seed({},dict(campaign=str(tmp_path),source_sha256='test'),None)
