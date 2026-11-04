import copy,json
from dataclasses import replace
import numpy as np
import pytest
from research.vectorized_backtest.v66 import agent_intervention as review
from research.vectorized_backtest.v66.genome import sample as raw_sample, Network, Neuron
from research.vectorized_backtest.v66.authority import construct, ALLOWED

def sample(rng,count):
    return [construct(replace(m,networks=dict(m.networks,entry=Network((Neuron((ALLOWED[0],),(1.,),0.,op="identity"),))))) for m in raw_sample(rng,count)]
from research.vectorized_backtest.v66.local_runner import CompactPedigree
from research.vectorized_backtest.v66.publication import write_json
from research.vectorized_backtest.v66.distributed_assets import sha


@pytest.fixture
def case(tmp_path):
    parent,fresh,elite=sample(np.random.default_rng(18),3)
    pedigree=CompactPedigree(3)
    old=pedigree.birth([parent],10,[(-1,-1)],['fresh'])
    ids=pedigree.birth([fresh,elite],11,[(-1,-1)]*2,['fresh_mutation','parent_mutation'])
    folder=tmp_path/'generation-0010';folder.mkdir()
    values={'population.json':dict(ids=old.tolist(),population=[parent.payload()]),
            'training.json':dict(ids=[0],panel={'score':[3.]}),'evaluated-pedigree.json':[pedigree.records[0]],'session-metrics.json':{}}
    for name,value in values.items():write_json(folder/name,value)
    write_json(folder/'complete.json',dict(population_sha256=sha(folder/'population.json'),
        training_sha256=sha(folder/'training.json'),evaluated_pedigree_sha256=sha(folder/'evaluated-pedigree.json'),
        observational_reports={'session-metrics.json':sha(folder/'session-metrics.json')}))
    # Deliberately unreadable validation: evidence/intake must never open it.
    (folder/'validation.json').write_text('NOT JSON')
    child=replace(parent,networks=dict(parent.networks,entry=replace(parent.networks['entry'],threshold=.12345)))
    package=dict(version=review.LEGACY_POLICY['version'],scope='training',review_epoch=10,source_generation=10,
        training_sha256='train',evidence=review.evidence(tmp_path,10)[1],rationale='Test a stricter entry',
        offspring=[dict(parent=0,parent_sha256=pedigree.records[0]['program_sha256'],payload=child.payload(),hypothesis='Threshold sensitivity')])
    inbox=tmp_path/'agent-reviews/inbox';inbox.mkdir(parents=True)
    write_json(inbox/'review-0010.json',package)
    return tmp_path,dict(agent_intervention=review.LEGACY_POLICY,training_sha256='train'),[fresh,elite],ids,pedigree,package


def test_intake_preserves_nonfresh_and_restarts_exactly(case):
    root,job,members,ids,pedigree,package=case
    before=copy.deepcopy(pedigree)
    got,newids,consumed,report=review.apply(root,job,11,members,ids,pedigree,{})
    assert got[1] is members[1] and newids[1]==ids[1]
    assert report['replaced_ids']==[int(ids[0])] and consumed['10']
    assert pedigree.records[int(newids[0])]['parents']==[0,-1]
    (root/'agent-reviews/inbox/review-0010.json').unlink()
    retry,rids,rconsumed,rreport=review.apply(root,job,11,members,ids,before,{})
    assert [m.payload() for m in retry]==[m.payload() for m in got]
    assert rids.tolist()==newids.tolist() and rreport==report and rconsumed==consumed
    _,_,_,later=review.apply(root,job,12,got,newids,pedigree,consumed)
    assert later is None


@pytest.mark.parametrize('change',[lambda p:p.update(scope='validation'),lambda p:p.update(training_sha256='other'),
    lambda p:p.update(review_epoch=9),lambda p:p['offspring'][0].update(parent_sha256='tampered'),
    lambda p:p.update(offspring=p['offspring']*65),lambda p:p.update(rationale='')])
def test_invalid_package_fails(case,change):
    root,job,_,_,_,package=case;change(package)
    with pytest.raises((ValueError,KeyError)):review.validate_package(root,job,package,11)


def test_evidence_tampering_and_stale_reviews_fail(case):
    root,job,_,_,_,package=case
    with pytest.raises(ValueError):review.validate_package(root,job,package,31)
    (root/'generation-0010/training.json').write_text('{} ')
    with pytest.raises(ValueError,match='evidence changed'):review.validate_package(root,job,package,11)


def test_empty_decision_does_not_change_after_arrival(case):
    root,job,members,ids,pedigree,package=case
    path=root/'agent-reviews/inbox/review-0010.json';path.unlink()
    assert review.apply(root,job,11,members,ids,pedigree,{})[3] is None
    write_json(path,package)
    assert review.apply(root,job,11,members,ids,pedigree,{})[3] is None


def test_duplicate_and_nonfresh_replacement_forbidden(case):
    root,job,members,ids,pedigree,package=case
    package['offspring'][0]['payload']=members[1].payload()
    write_json(root/'agent-reviews/inbox/review-0010.json',package)
    with pytest.raises(ValueError,match='duplicates the current'):review.apply(root,job,11,members,ids,pedigree,{})


def test_policy_transition_cannot_change_selection(tmp_path):
    from research.vectorized_backtest.v66.resume_authority import global_predecessor,accept_checkpoint
    from research.vectorized_backtest.v66.identity import digest
    old=dict(version='v66-global-v1',source_sha256='old',machines={'workstation':{'code':'old'}},selection={'elite':.1})
    path=tmp_path/'old.json';write_json(path,old)
    state=dict(completed=10,seals={});contract=dict(source_sha256='old',selection=old['selection'])
    saved=dict(contract=contract,state=state,state_sha256=digest(state));cp=tmp_path/'checkpoint.json';write_json(cp,saved)
    job=dict(old,source_sha256='new',agent_intervention=review.POLICY,resume_transition=dict(
        predecessor_job=str(path),predecessor_job_sha256=sha(path),agent_intervention_change=review.POLICY,
        global_checkpoint=dict(checkpoint_sha256=sha(cp),state_sha256=digest(state),contract_sha256=digest(contract))))
    expected=dict(contract,source_sha256='new',agent_intervention=review.POLICY)
    accept_checkpoint(job,cp,saved,expected)
    bad=copy.deepcopy(job);bad['selection']['elite']=.5
    with pytest.raises(ValueError):global_predecessor(bad)
    with pytest.raises(ValueError):accept_checkpoint(job,cp,saved,dict(expected,extra='unauthorized'))


def test_resume_preserves_founder_cache_authority(tmp_path):
    from research.vectorized_backtest.v66.founder_readmission import verify_cache_bindings
    original={'first':{'source':'founder'}};bound={'result_caches':original}
    previous=dict(version='v66-global-v1',source_sha256='prior',founder_readmission=bound,
                  result_cache_transition=original,machines={'workstation':{'code':'old'}})
    path=tmp_path/'previous.json';write_json(path,previous)
    job=dict(previous,source_sha256='new',result_cache_transition=dict(original,current={'source':'prior'}),
        resume_transition=dict(predecessor_job=str(path),predecessor_job_sha256=sha(path)))
    verify_cache_bindings(job,bound)
    bad=copy.deepcopy(job);bad['result_cache_transition']['current']['source']='unrelated'
    with pytest.raises(ValueError):verify_cache_bindings(bad,bound)
    bad=copy.deepcopy(job);bad['result_cache_transition']['first']['source']='changed'
    with pytest.raises(ValueError):verify_cache_bindings(bad,bound)


def test_append_256_preserves_normal_population_and_retry(case):
    root,job,members,ids,pedigree,package=case
    parent=sample(np.random.default_rng(21),1)[0]
    parents=[replace(parent,networks=dict(parent.networks,entry=replace(parent.networks['entry'],threshold=-.9+i/1000))) for i in range(256)]
    pedigree=CompactPedigree(3);parent_ids=pedigree.birth(parents,15,[(-1,-1)]*256,['crossover']*256)
    normal=[members[0]]*1024
    normal_ids=pedigree.birth(normal,16,[(0,-1)]*1024,['parent_mutation']*1024)
    folder=root/'generation-0015';folder.mkdir()
    for name,value in {'population.json':dict(ids=parent_ids.tolist(),population=[p.payload() for p in parents]),
        'training.json':dict(ids=parent_ids.tolist(),panel={'score':list(range(256))}),
        'evaluated-pedigree.json':[pedigree.records[int(i)] for i in parent_ids], 'session-metrics.json':{}}.items():write_json(folder/name,value)
    write_json(folder/'complete.json',dict(population_sha256=sha(folder/'population.json'),training_sha256=sha(folder/'training.json'),
        evaluated_pedigree_sha256=sha(folder/'evaluated-pedigree.json'),observational_reports={'session-metrics.json':sha(folder/'session-metrics.json')}))
    (folder/'validation.json').write_text('NEVER READ VALIDATION')
    package.update(version=review.POLICY['version'],review_epoch=15,source_generation=15,evidence=review.evidence(root,15)[1],offspring=[])
    for i,p in enumerate(parents):
        child=replace(p,networks=dict(p.networks,entry=replace(p.networks['entry'],threshold=.2+i/1000)))
        package['offspring'].append(dict(parent=i,parent_sha256=pedigree.records[i]['program_sha256'],payload=child.payload(),hypothesis='Training-supported repair'))
    (root/'agent-reviews/inbox/review-0010.json').unlink()
    write_json(root/'agent-reviews/inbox/review-0015.json',package)
    job.update(agent_intervention=review.POLICY,population=1024)
    before=copy.deepcopy(pedigree)
    got,newids,consumed,report=review.apply(root,job,16,normal,normal_ids,pedigree,{})
    assert len(got)==1280 and len(newids)==1280 and len(set(newids))==1280
    assert all(a is b for a,b in zip(got[:1024],normal))
    assert np.array_equal(newids[:1024],normal_ids) and report['replaced_ids']==[]
    assert report['successor_population']==1024 and len(report['new_ids'])==256
    retry,rids,rconsumed,rreport=review.apply(root,job,16,normal,normal_ids,before,{})
    assert [x.payload() for x in retry]==[x.payload() for x in got]
    assert np.array_equal(rids,newids) and rreport==report and consumed==rconsumed
    bad=copy.deepcopy(package);bad['offspring'][1]['parent']=0;bad['offspring'][1]['parent_sha256']=bad['offspring'][0]['parent_sha256']
    with pytest.raises(ValueError,match='concentration'):review.validate_package(root,job,bad,16)
    with pytest.raises(ValueError,match='stale'):review.validate_package(root,job,package,26)


def test_review_selection_covers_256_evolved_behaviors_in_one_recorded_family():
    from research.vectorized_backtest.v66.agent_review_packet import select_candidates
    n=512;ids=list(range(n));panel=dict(eligible=[True]*n,closed_positions=[10]*n,score=list(range(n)))
    sessions={d:dict(net_pnl=(np.arange(n)*sign).tolist(),score=list(range(n)),closed_positions=[10]*n,
        winning_positions=[2]*n,blocked_requests=[0]*n) for d,sign in [('a',1),('b',-0.5)]}
    records=[dict(node_count=2**(i%8),family_contributions=[1],operation='crossover') for i in ids]
    got,summary=select_candidates(ids,panel,sessions,records,256)
    assert len(got)==256 and len(set(got))==256 and summary['shortfall']==0
    assert len({records[i]['node_count'] for i in got})==8
    assert got==select_candidates(ids,panel,sessions,records,256)[0]
    for d in sessions:
        for k in sessions[d]:sessions[d][k]=[sessions[d][k][0]]*n
    got,summary=select_candidates(ids,panel,sessions,records,256)
    assert len(got)==1 and summary['shortfall']==255


def test_only_explicit_legacy_to_append_transition_allowed():
    review.check_transition(None,review.LEGACY_POLICY,review.LEGACY_POLICY)
    change=dict(before=review.LEGACY_POLICY,after=review.POLICY)
    review.check_transition(review.LEGACY_POLICY,review.POLICY,change)
    with pytest.raises(ValueError):review.check_transition(review.LEGACY_POLICY,review.POLICY,review.POLICY)
    with pytest.raises(ValueError):review.check_transition(review.POLICY,review.LEGACY_POLICY,change)
