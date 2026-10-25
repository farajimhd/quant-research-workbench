from datetime import datetime, timezone
import json
import pytest
from pipelines.fundamentals import recovery
from pipelines.certified_research.contracts import digest

C='0000000001'


def test_compact_inventory_preserves_old_identity_changes_and_rejects_bad_results():
    now=datetime(2026,10,25,tzinfo=timezone.utc)
    row=dict(cik=C,fingerprint='A'*64,entries=3)
    def read(rows):return recovery.source_inventory(lambda sql:rows,[C],now)
    original=read([row])
    assert original==read([dict(row)])
    assert original!=read([dict(row,fingerprint='B'*64)])
    assert original!=read([dict(row,entries=4)])
    assert original!=read([])
    for bad in ([row,row],[dict(row,cik='0000000002')],
                [dict(row,fingerprint='truncated')],[dict(row,entries=0)]):
        with pytest.raises(ValueError):read(bad)


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


def test_registered_fund_is_not_an_unresolved_corporate_identity():
    case=recovery.reconcile([C],[],[dict(cik=C,forms=['N-CSR','NPORT-P','N-CEN'])])[0]
    assert case['lane']=='not_applicable_corporate_v1'
    assert case['reason']=='registered_fund_reporting_regime'
    assert not case['requires_review'] and not case['full_history_complete']
    # A later corporate report invalidates that applicability disposition.
    changed=recovery.reconcile([C],[],[dict(cik=C,forms=['N-CSR','10-K'])])[0]
    assert changed['lane']=='extraction' and changed['requires_review']
    assert recovery.reconcile([C],[],[dict(cik=C,forms=['N-2'])])[0]['requires_review']


def test_debt_variants_are_not_simultaneous_required_features():
    row=dict(cik=C,period_start=None,period_end='2026-06-30',unit='USD',
             present=['assets','debt_total'],conflicts=[],accessions=['a'])
    context=recovery.reconcile([C],[row],[])[0]['contexts'][0]
    assert not context['debt_review_required']
    assert not set(context['missing_candidates'])&recovery.DEBT_VARIANTS
    assert 'debt_noncurrent' in context['unreported_debt_variants']
    row['present']=['assets']
    context=recovery.reconcile([C],[row],[])[0]['contexts'][0]
    assert context['debt_review_required']


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


def test_native_mapping_change_invalidates_unchanged_coverage(tmp_path,monkeypatch):
    from pipelines.fundamentals import native_features
    monkeypatch.setattr(recovery,'source_inventory',lambda q,ids,*a:{c:'same' for c in ids})
    monkeypatch.setattr(recovery,'dependency_inventory',lambda *a:'same')
    mapping=['first']
    monkeypatch.setattr(native_features,'mapping_hash',lambda ciks=None:mapping[0])
    def save(p,v):p.write_text(json.dumps(v))
    def run():return recovery.write_queue(lambda sql:[],tmp_path,[C],'2019-01-01','2026-10-02',
        datetime(2026,10,25,tzinfo=timezone.utc),save=save)
    assert run()['updated']==1
    assert run()['unchanged']==1
    mapping[0]='second'
    assert run()['updated']==1


def test_native_mapping_only_reaudits_affected_issuer(tmp_path,monkeypatch):
    from pipelines.fundamentals import native_features
    other='0000000002';mapping={C:'a',other:'b'};reads=[]
    monkeypatch.setattr(recovery,'source_inventory',lambda q,ids,*a:{c:'same' for c in ids})
    monkeypatch.setattr(recovery,'dependency_inventory',lambda *a:'same')
    monkeypatch.setattr(native_features,'mapping_hash',lambda ids:mapping[ids[0]])
    def save(p,v):p.write_text(json.dumps(v))
    def query(sql):reads.append(sql);return []
    def run():return recovery.write_queue(query,tmp_path,[C,other],'2019-01-01','2026-10-02',
        datetime(2026,10,25,tzinfo=timezone.utc),save=save)
    assert run()['updated']==2
    preserved=(tmp_path/'cases'/(other+'.json')).read_bytes();reads.clear();mapping[C]='changed'
    result=run()
    assert result['updated']==1 and result['unchanged']==1
    assert all(other not in sql for sql in reads)
    assert (tmp_path/'cases'/(other+'.json')).read_bytes()==preserved


def test_only_registered_unpublished_originals_are_queued():
    from pipelines.certified_research.sec_html_reviewed_spac_native import REVIEWED
    key,plan=next(iter(REVIEWED.items()));cik=plan['cik']
    row=dict(cik=cik,accession='0000000001-21-000001',document='filing.htm',content_sha256=key,
             day='2021-03-17',form='S-1/A',source_version_key='')
    queries=[]
    def query(sql):queries.append(sql);return [row,dict(row,document='unknown.htm',content_sha256='0'*64)]
    assert recovery.reviewed_documents(query,[cik],'2019-01-01','2026-10-02')==[row]
    assert 'NOT IN' in queries[0] and 'sec_original_qualified' in queries[0]
    with pytest.raises(ValueError,match='competing'):
        recovery.reviewed_documents(lambda q:[row,row],[cik],'2019-01-01','2026-10-02')


def test_spac_native_does_not_correct_an_inconsistent_reported_total():
    from pipelines.certified_research.sec_html_reviewed_spac_native import REVIEWED
    assert len(REVIEWED)==4 and sum(len(p['facts']) for p in REVIEWED.values())==20
    sunfire=next(p for p in REVIEWED.values() if p['cik']=='0001873885')
    prior={f['label']:f['expected_value'] for f in sunfire['facts'] if f['period_end']=='2021-12-31'}
    assert prior['Total Assets']=='669200'
    assert int(prior['Total Current Liabilities'])+int(prior['Total Shareholder’s Equity'])==669220


def test_atomic_receipt_save_does_not_extend_a_long_hash_filename(tmp_path):
    from scripts.run_fundamentals import save
    parent=tmp_path/('p'*max(1,180-len(str(tmp_path))-1));parent.mkdir()
    path=parent/('a'*64+'.json')
    assert len(str(path))<260 and len(str(path))+38>260
    save(path,dict(preserved=True));save(path,dict(preserved=True,reused=True))
    assert json.loads(path.read_text())==dict(preserved=True,reused=True)
    assert list(parent.iterdir())==[path]
