from datetime import datetime, timezone
import json
import pytest
from pipelines.fundamentals import recovery
from pipelines.certified_research.contracts import digest

C='0000000001'


def test_period_and_units_are_not_mixed_or_declared_complete():
    row=dict(cik=C,period_start=None,period_end='2026-06-30',unit='USD',present=['assets'],conflicts=[],accessions=['a'])
    case=recovery.reconcile([C],[row],[dict(cik=C,forms=['S-1'])])[0]
    assert case['lane']=='extraction'
    assert not case['full_history_complete']
    assert 'cash' in case['contexts'][0]['missing_candidates']
    assert 'revenue' not in case['contexts'][0]['missing_candidates']
    assert 'shares_outstanding' not in case['contexts'][0]['missing_candidates']
    row.update(unit='shares',present=['shares_outstanding'])
    assert recovery.reconcile([C],[row],[])[0]['contexts'][0]['missing_candidates']==[]


def test_competing_identity_and_ubs_are_rejected():
    with pytest.raises(ValueError):recovery.reconcile([C],[],[dict(cik=C),dict(cik=C)])
    with pytest.raises(ValueError):recovery.reconcile(['0001610520'],[],[])


def test_queue_preserves_unchanged_cases_and_detects_changed_evidence(tmp_path,monkeypatch):
    fingerprint=['a']; dependency=['d'];reads=[]
    monkeypatch.setattr(recovery,'source_inventory',lambda q,ids,*a:{c:fingerprint[0] for c in ids})
    monkeypatch.setattr(recovery,'dependency_inventory',lambda *a:dependency[0])
    def query(sql):
        reads.append(sql)
        return []
    def save(path,value):path.write_text(json.dumps(value))
    now=datetime(2026,10,25,12,tzinfo=timezone.utc)
    def run():return recovery.write_queue(query,tmp_path,[C],'2019-01-01','2026-10-02',now,save=save)
    assert run()['updated']==1
    path=tmp_path/'cases'/(C+'.json');before=path.read_bytes();reads.clear()
    assert run()['unchanged']==1 and not reads and path.read_bytes()==before
    fingerprint[0]='b'
    assert run()['updated']==1 and len(reads)==3
    dependency[0]='e';assert run()['updated']==1
    value=json.loads(path.read_text());value['reason']='silently complete';save(path,value)
    with pytest.raises(ValueError,match='receipt changed'):run()


def test_source_changes_do_not_publish_a_case(tmp_path,monkeypatch):
    calls=iter(['a','b'])
    monkeypatch.setattr(recovery,'source_inventory',lambda *a:{C:next(calls)})
    monkeypatch.setattr(recovery,'dependency_inventory',lambda *a:'d')
    def save(p,v):p.write_text(json.dumps(v))
    with pytest.raises(ValueError,match='sources changed'):
        recovery.write_queue(lambda sql:[],tmp_path,[C],'2019-01-01','2026-10-02',datetime.now(timezone.utc),save=save)
    assert not list((tmp_path/'cases').iterdir())
