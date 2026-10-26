import json,hashlib
from types import SimpleNamespace
from datetime import datetime,timezone
from services.gateway_core.fundamentals_handoff import notify
from scripts import run_gateway_fundamentals as runner


def setup(root):
    population=root/'population.json';population.write_text('{}')
    request=dict(population=str(population),population_sha256=hashlib.sha256(population.read_bytes()).hexdigest(),
        endpoint='http://example',since_recorded='2026-10-26T00:00:00+00:00',workers=4)
    (root/'controller-request.json').write_text(json.dumps(request))
    return request


def test_notification_requires_reviewed_controller_and_coalesces(tmp_path):
    assert not notify('sec',directory=tmp_path)
    setup(tmp_path)
    assert notify('sec',directory=tmp_path,detail='one')
    first=json.loads((tmp_path/'sec-commit.json').read_text())
    assert notify('sec',directory=tmp_path,detail='two')
    second=json.loads((tmp_path/'sec-commit.json').read_text())
    assert second['generation']!=first['generation'] and second['detail']=='two'
    assert len(list(tmp_path.glob('*-commit.json')))==1


def test_no_lost_commit_during_publication_or_repeated_unchanged_run(tmp_path,monkeypatch):
    setup(tmp_path);notify('sec',directory=tmp_path)
    import scripts.run_fundamentals as shared
    monkeypatch.setattr(runner.platform,'node',lambda:'DESKTOP-SAAI85T')
    monkeypatch.setattr(shared,'Client',lambda *a:SimpleNamespace(query=lambda sql:[],close=lambda:None))
    calls=[]
    def publish(command,**kwargs):
        calls.append(command)
        if len(calls)==1:notify('sec',directory=tmp_path,detail='committed while publishing')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(runner.subprocess,'run',publish)
    assert runner.run(tmp_path)==0
    assert runner.run(tmp_path)==0 and len(calls)==2
    assert runner.run(tmp_path)==0 and len(calls)==2


def test_population_change_blocks_and_failure_has_backoff(tmp_path,monkeypatch):
    request=setup(tmp_path)
    (tmp_path/'population.json').write_text('{"changed":true}')
    import pytest
    with pytest.raises(ValueError,match='population changed'):runner.command(request,tmp_path)


def test_metadata_backstop_detects_commit_without_notification(tmp_path,monkeypatch):
    setup(tmp_path)
    import scripts.run_fundamentals as shared
    version=[1];calls=[]
    monkeypatch.setattr(runner.platform,'node',lambda:'DESKTOP-SAAI85T')
    monkeypatch.setattr(shared,'Client',lambda *a:SimpleNamespace(query=lambda sql:[dict(rows=version[0])],close=lambda:None))
    monkeypatch.setattr(runner.subprocess,'run',lambda *a,**k:(calls.append(a) or SimpleNamespace(returncode=0)))
    runner.run(tmp_path);runner.run(tmp_path)
    assert len(calls)==1
    version[0]=2;runner.run(tmp_path)
    assert len(calls)==2


def test_bank_review_does_not_repeat_successful_other_source_publication(tmp_path,monkeypatch):
    request=setup(tmp_path);request['fdic']=dict(source_root=str(tmp_path/'fdic-source'))
    (tmp_path/'controller-request.json').write_text(json.dumps(request))
    import scripts.run_fundamentals as shared
    from services.reference_gateway import fdic_maintenance
    import pytest
    monkeypatch.setattr(runner.platform,'node',lambda:'DESKTOP-SAAI85T')
    monkeypatch.setattr(shared,'Client',lambda *a:SimpleNamespace(query=lambda sql:[],close=lambda:None))
    calls=[]
    monkeypatch.setattr(runner.subprocess,'run',lambda *a,**k:(calls.append(a) or SimpleNamespace(returncode=0)))
    def blocked(*a):raise ValueError('bank review required')
    monkeypatch.setattr(fdic_maintenance,'refresh',blocked)
    with pytest.raises(ValueError,match='bank review'):runner.run(tmp_path)
    state=json.loads((tmp_path/'controller-state.json').read_text())
    assert state['publication_phase']=='complete' and len(calls)==1
    state['retry_after']='2000-01-01T00:00:00+00:00'
    (tmp_path/'controller-state.json').write_text(json.dumps(state))
    monkeypatch.setattr(fdic_maintenance,'refresh',lambda *a:dict(reports=0))
    assert runner.run(tmp_path)==0 and len(calls)==1
