import copy,json
from dataclasses import replace
import numpy as np
import pytest
from research.vectorized_backtest.v65 import agent_intervention as review
from research.vectorized_backtest.v65.genome import sample
from research.vectorized_backtest.v65.local_runner import CompactPedigree
from research.vectorized_backtest.v65.publication import write_json
from research.vectorized_backtest.v65.distributed_assets import sha


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
    package=dict(version=review.POLICY['version'],scope='training',review_epoch=10,source_generation=10,
        training_sha256='train',evidence=review.evidence(tmp_path,10)[1],rationale='Test a stricter entry',
        offspring=[dict(parent=0,parent_sha256=pedigree.records[0]['program_sha256'],payload=child.payload(),hypothesis='Threshold sensitivity')])
    inbox=tmp_path/'agent-reviews/inbox';inbox.mkdir(parents=True)
    write_json(inbox/'review-0010.json',package)
    return tmp_path,dict(agent_intervention=review.POLICY,training_sha256='train'),[fresh,elite],ids,pedigree,package


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
    from research.vectorized_backtest.v65.resume_authority import global_predecessor,accept_checkpoint
    from research.vectorized_backtest.v65.identity import digest
    old=dict(version='v65-global-v1',source_sha256='old',machines={'workstation':{'code':'old'}},selection={'elite':.1})
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
