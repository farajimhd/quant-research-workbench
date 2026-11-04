import copy
import json
from types import SimpleNamespace
import pytest
from research.vectorized_backtest.v66 import review_window as w, review_notes as n
from research.vectorized_backtest.v66.publication import write_json
from research.vectorized_backtest.v66.distributed_assets import sha
from research.vectorized_backtest.v66.identity import digest
from research.vectorized_backtest.v66.resume_authority import accept_checkpoint, global_predecessor


def test_bounded_live_wait_and_restart_keep_resources(tmp_path):
    job=dict(campaign=str(tmp_path),phase2_generations=300,review_window=w.SETTINGS)
    root=tmp_path/'global';root.mkdir()
    now=[100.];events=[];resident=object();worker=SimpleNamespace(data=resident)
    def sleep(seconds):now[0]+=seconds;assert worker.data is resident
    result=w.wait_for_review(root,job,5,lambda **kw:events.append(kw),clock=lambda:now[0],sleep=sleep,exporter=lambda *a:None)
    assert result['status']=='timed_out' and now[0]==220.
    w.wait_for_review(root,job,5,lambda **kw:pytest.fail('Reopened'),clock=lambda:500.,exporter=lambda *a:pytest.fail('Reexported'))
    assert worker.data is resident
    assert w.due(job,5) and not w.due(job,4) and not w.due(job,300)


def test_submission_releases_wait_without_replay(tmp_path):
    job=dict(campaign=str(tmp_path),phase2_generations=300,review_window=w.SETTINGS)
    root=tmp_path/'global';root.mkdir();now=[100.]
    def submit(seconds):
        path=root/'agent-reviews/epoch-0005/window.json'
        state=json.loads(path.read_bytes());state['status']='submitted';write_json(path,state)
    result=w.wait_for_review(root,job,5,lambda **kw:None,clock=lambda:now[0],sleep=submit,exporter=lambda *a:None)
    assert result['status']=='submitted'


def test_notes_use_existing_evidence_not_endpoint_replay(tmp_path):
    parents=[dict(serial=7,record={'program_sha256':'p'})]
    write_json(tmp_path/'parents.json',parents);write_json(tmp_path/'training-evidence.json',{'losses':3})
    row=dict(serial=7,parent_sha256='p',status='reviewed',reason='Interpreted repeated losses',
        evidence=[dict(path='training-evidence.json',sha256=sha(tmp_path/'training-evidence.json'))],
        hypothesis='Reduce repeated entries',expected_change='Lower turnover',tradeoff='Miss recovery',falsification='Net profit falls')
    notes=dict(version=n.VERSION,scope='training',source_generation=5,parents_sha256=sha(tmp_path/'parents.json'),
        summary='Repair hypothesis',prior_outcomes='No admitted prior edits',candidates=[row])
    package=dict(source_generation=5,offspring=[dict(parent=7)])
    assert n.validate(tmp_path,package,notes)['proposed']==1
    row['status']='screened'
    with pytest.raises(ValueError,match='interpreted'):n.validate(tmp_path,package,notes)
    assert n.validate(tmp_path,dict(package,offspring=[]),notes)['screened']==1
    row['status']='reviewed';write_json(tmp_path/'training-evidence.json',{'losses':4})
    with pytest.raises(ValueError,match='changed'):n.validate(tmp_path,package,notes)


def test_resume_only_adds_window_preserving_sealed_checkpoint(tmp_path):
    old=dict(version='v66-global-v1',source_sha256='old',phase2_generations=300,machines={'workstation':{'code':'old'}},selection={'elite':.1})
    write_json(tmp_path/'old.json',old)
    state=dict(completed=49);contract=dict(source_sha256='old',generations=300,selection=old['selection'])
    saved=dict(state=state,contract=contract,state_sha256=digest(state));cp=tmp_path/'checkpoint.json';write_json(cp,saved)
    job=dict(old,source_sha256='new',review_window=w.SETTINGS,resume_transition=dict(
        predecessor_job=str(tmp_path/'old.json'),predecessor_job_sha256=sha(tmp_path/'old.json'),
        review_window_change=dict(before=None,after=w.SETTINGS),
        global_checkpoint=dict(checkpoint_sha256=sha(cp),state_sha256=digest(state),contract_sha256=digest(contract))))
    before=cp.read_bytes();accept_checkpoint(job,cp,saved,dict(contract,source_sha256='new',review_window=w.SETTINGS))
    assert cp.read_bytes()==before
    with pytest.raises(ValueError):global_predecessor(dict(job,selection={'elite':.5}))
    bad=copy.deepcopy(job);bad['review_window']['seconds']=600
    with pytest.raises(ValueError):global_predecessor(bad)


def test_checked_publisher_and_wait_share_live_window(tmp_path,monkeypatch):
    from research.vectorized_backtest.v66 import review_contract as rc, agent_intervention as ai
    job=dict(campaign=str(tmp_path),phase2_generations=300,review_window=w.SETTINGS)
    root=tmp_path/'phase-2/global';root.mkdir(parents=True)
    packet=root/'agent-reviews/epoch-0005';packet.mkdir(parents=True)
    write_json(root/'checkpoint.json',dict(state=dict(completed=5,agent_reviews={})))
    write_json(packet/'window.json',dict(status='reviewing',deadline_epoch=200.,review_epoch=5,source_generation=5))
    package=dict(review_epoch=5,source_generation=5,offspring=[])
    write_json(packet/'package.json',package);write_json(packet/'review-notes.json',dict(summary='No supported repairs'))
    monkeypatch.setattr(rc.time,'time',lambda:100.)
    monkeypatch.setattr(n,'validate',lambda *a:dict(selected=256,reviewed=0,screened=256,not_reviewed=0,proposed=0))
    calls=[];monkeypatch.setattr(ai,'validate_package',lambda *a:calls.append(a[-1]))
    rc.publish(packet,job)
    assert calls==[6]
    assert json.loads((packet/'window.json').read_bytes())['status']=='no_proposals'
    assert json.loads((root/'agent-reviews/inbox/epoch-0005.json').read_bytes())==package
    with pytest.raises(ValueError,match='closed'):rc.publish(packet,job)


def test_expired_window_rejects_without_intake(tmp_path,monkeypatch):
    from research.vectorized_backtest.v66 import review_contract as rc
    packet=tmp_path/'phase-2/global/agent-reviews/epoch-0005';packet.mkdir(parents=True)
    write_json(packet/'window.json',dict(status='reviewing',deadline_epoch=1.,review_epoch=5,source_generation=5))
    write_json(packet/'package.json',dict(review_epoch=5,source_generation=5));write_json(packet/'review-notes.json',{})
    with pytest.raises(ValueError,match='closed'):rc.publish(packet,dict(campaign=str(tmp_path),review_window=w.SETTINGS))
    assert not (packet.parent/'inbox').exists()
