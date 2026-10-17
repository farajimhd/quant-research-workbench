"""V21 preservation, exact control and explicitly added reproduction behavior."""
from dataclasses import asdict, replace
from pathlib import Path
import ast
import numpy as np
import pytest
import torch
from research.vectorized_backtest.v21 import genome as old_genome, lineage as old_lineage, objectives as old_objectives
from research.vectorized_backtest.v27 import genome, lineage, objectives, composition, behavior
from test_vector_v27 import metrics


def test_initial_candidates_and_rng_exact_v21():
    from research.vectorized_backtest.v21.initial_population import initialize as old_initialize
    from research.vectorized_backtest.v27.initial_population import initialize
    a,b=np.random.default_rng(2020),np.random.default_rng(2020)
    old,old_report=old_initialize(a,128);new,new_report=initialize(b,128)
    assert [m.payload() for m in old]==[m.payload() for m in new]
    assert a.bit_generator.state==b.bit_generator.state
    for key in ('seed_count','random_count','unique_count','duplicate_rejections'):assert old_report[key]==new_report[key]


def test_legacy_crossover_and_mutation_exact_payload_and_rng():
    old=old_genome.sample(np.random.default_rng(13),2);new=[genome.Individual.restore(m.payload()) for m in old]
    successes=0
    for seed in range(32):
        a,b=np.random.default_rng(seed),np.random.default_rng(seed)
        try:expected=old_genome.crossover(a,*old)
        except ValueError:
            with pytest.raises(ValueError):composition.cross(b,*new,operator='v21_legacy')
            continue
        actual,op=composition.cross(b,*new,operator='v21_legacy')
        assert op=='v21_legacy' and expected.payload()==actual.payload()
        assert a.bit_generator.state==b.bit_generator.state
        successes+=1
    assert successes>=16
    from research.vectorized_backtest.v21.variation import mutate_fields as old_mutate
    for count in range(1,6):
        a,b=np.random.default_rng(count),np.random.default_rng(count)
        expected,edits=old_mutate(a,old[0],count)
        actual,new_edits=composition.mutation(b,new[0],count,True)
        assert expected.payload()==actual.payload() and edits==new_edits
        assert a.bit_generator.state==b.bit_generator.state


def test_control_full_reproduction_matches_v21():
    a,b=np.random.default_rng(2121),np.random.default_rng(2121)
    old=old_genome.sample(a,32);new=genome.sample(b,32)
    p,q=old_lineage.Pedigree(),lineage.Pedigree()
    ids=p.birth(old,1,[(-1,-1)]*32,['fresh']*32);newids=q.birth(new,1,[(-1,-1)]*32,['fresh']*32)
    args=dict(session_values=None,session_eligible=None,global_values=np.arange(32.),global_eligible=np.ones(32,bool))
    control=lineage.Selection(legacy_crossover_fraction=1.,behavior_pairing=False)
    for generation in (2,3):
        old,ids,r=old_lineage.next_population(a,old,ids,p,[],generation,old_lineage.Selection(),**args)
        new,newids,s=lineage.next_population(b,new,newids,q,[],generation,control,**args)
        assert [m.payload() for m in old]==[m.payload() for m in new]
        assert ids.tolist()==newids.tolist() and a.bit_generator.state==b.bit_generator.state
        for key in r:assert r[key]==s[key],key


def test_v21_objectives_exact_across_sessions():
    rows=[metrics([[1000.,-100.],[],[-50.]]),metrics([[-200.],[50.,40.],[100.]])]
    rows[0]['drawdown']=[40.,0.,10.]
    assert asdict(objectives.Objective())==asdict(old_objectives.Objective())
    old=old_objectives.score(rows,[0,20,30]);new=objectives.score(rows,[0,20,30])
    def equal(a,b):
        if isinstance(a,dict):
            assert a.keys()==b.keys()
            for k in a:equal(a[k],b[k])
        else:np.testing.assert_array_equal(a,b)
    equal(old,new)
    assert not np.array_equal(new['score'],new['total_pnl'])


def test_operator_mix_and_bounded_training_pairing(monkeypatch):
    rng=np.random.default_rng(100);members=genome.sample(rng,192);p=lineage.Pedigree();ids=p.birth(members,1,[(-1,-1)]*192,['fresh']*192)
    m=metrics([[1.]]*192)
    m['episode_entry_clocks']=[[i%30] for i in range(192)];m['episode_exit_clocks']=[[i%30+10] for i in range(192)]
    index=behavior.BehaviorIndex([m]);called=[]
    original=composition.cross
    def tracked(*args,**kwargs):
        child,op=original(*args,**kwargs);called.append(op);return child,op
    monkeypatch.setattr(composition,'cross',tracked)
    children,newids,report=lineage.next_population(rng,members,ids,p,[],2,session_values=None,session_eligible=None,
        global_values=np.arange(192.),global_eligible=np.ones(192,bool),behavior=index)
    births=[p.records[int(i)] for i in newids if int(i)>=192]
    crossed=[r for r in births if r['composition_operator']!='fresh']
    assert report['crossovers']==len(crossed) and report['elites']==19 and report['fresh_candidates']==39
    assert sum(report['operator_counts'].values())==len(births)
    assert all(report['behavior_requested_counts'][route]>0 for route in ('similar','dissimilar','random'))
    assert all(r['parent_similarity'] is not None for r in crossed)
    assert any(r['composition_operator']=='v21_legacy' for r in crossed)
    assert any(c.routing is not None for c in children)
    assert set(called)<=set(composition.OPERATORS) and index.exact_queries<=behavior.MAXIMUM_PAIR_CACHE
    assert .35<called.count('v21_legacy')/len(called)<.65


def test_directional_cache_and_validation_rejection():
    m=dict(net_pnl=[1,1],episode_listings=[[0],[0]],episode_entry_clocks=[[0],[0]],episode_exit_clocks=[[20],[5]])
    index=behavior.BehaviorIndex([m]);reverse=index.similarity(1,0);forward=index.similarity(0,1)
    assert reverse['coverage_left']==forward['coverage_right']==1
    assert reverse['coverage_right']==forward['coverage_left']==.25 and index.exact_queries==1
    with pytest.raises(ValueError,match='training'):behavior.BehaviorIndex([m],split='validation')


def test_settings_bind_nominal_weights_and_control():
    from research.vectorized_backtest.v21.run_search import source_hash
    settings=composition.settings(lineage.Selection())
    assert settings['baseline_source_sha256']==source_hash()
    assert settings['operator_weights']['v21_legacy']==.5
    assert len(settings['operator_weights'])==14
    assert sum(settings['operator_weights'].values())==pytest.approx(1)
    assert settings['partner_requests']=={r:1/3 for r in ('similar','dissimilar','random')}
    control=composition.settings(lineage.Selection(legacy_crossover_fraction=1,behavior_pairing=False))
    assert control['operator_weights']['v21_legacy']==1 and not control['partner_requests']


def test_certified_source_guard_rejects_line_ending_drift(tmp_path):
    from research.vectorized_backtest.v27.certified_sources import verify,RAW_SHA256
    root=Path(genome.__file__).parent
    for name in RAW_SHA256:(tmp_path/name).write_bytes((root/name).read_bytes())
    verify(tmp_path)
    p=tmp_path/'enriched_math.py';p.write_bytes(p.read_bytes().replace(b'\n',b'\r\n'))
    with pytest.raises(ValueError,match='bytes changed'):verify(tmp_path)


def test_launch_gate_blocks_active_v26_for_fresh_and_cached_proof(tmp_path,monkeypatch):
    from research.vectorized_backtest.v27 import completion_gate
    from test_vector_v27 import predecessor_fixture
    from research.vectorized_backtest.v6.torch_backtest.runtime import write_json
    root=tmp_path/'v19/c/x'
    job=predecessor_fixture(root)
    monkeypatch.setattr(completion_gate,'alive',lambda owner:owner.get('pid')==26)
    proof=completion_gate.require_complete(job)
    pointer=tmp_path/'torch_backtest_v26/active-campaign.json'
    write_json(pointer,dict(pid=26,creation_time=1,campaign='active-v26'))
    assert completion_gate.completion(job)['status']=='waiting'
    with pytest.raises(RuntimeError,match='V26'):completion_gate.require_complete(job,proof)


def test_no_baseline_drift_in_financial_step_and_feature_producers():
    import inspect
    from research.vectorized_backtest.v21 import evaluator as old
    from research.vectorized_backtest.v27 import evaluator as new
    assert ast.dump(ast.parse(inspect.getsource(old.step)))==ast.dump(ast.parse(inspect.getsource(new.step)))
    root=Path(genome.__file__).parent.parent
    for name in ('features.py','indicator_math.py','enriched_math.py','indicator_identity.py','search_space.py'):
        assert (root/'v21'/name).read_text(encoding='utf8')==(root/'v27'/name).read_text(encoding='utf8')
    from research.vectorized_backtest.v27.certified_sources import verify
    verify()


def test_plain_and_same_leaf_routed_replay_match_v21():
    from research.vectorized_backtest.v21.evaluator import replay_cohort as old_replay
    from research.vectorized_backtest.v27.evaluator import replay_cohort
    from research.vectorized_backtest.v7.qualify import synthetic
    old=old_genome.sample(np.random.default_rng(12),1)[0]
    old=replace(old,policy=replace(old.policy,swing_left=0,swing_right=0,stop_fraction=.1,target_fraction=.9,cooldown=1,maximum_adds=0))
    new=genome.Individual.restore(old.payload());item=synthetic('cpu')
    gates=torch.tensor([1,0,0,0,0,0],dtype=torch.uint8)[None,:,None]
    expected=old_replay([item],[old],[gates],trace=True)[0]
    for member,signals in ((new,gates),(composition.routed('priority',new,new),gates.to(torch.int16)|(gates.to(torch.int16)<<5))):
        result=replay_cohort([item],[member],[signals],trace=True)[0]
        for key,value in expected.items():
            if isinstance(value,torch.Tensor):assert torch.equal(value,result[key]),key
            else:assert value==result[key],key
