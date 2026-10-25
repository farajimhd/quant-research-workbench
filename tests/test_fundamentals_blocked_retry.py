import json
from datetime import date
import pytest
from pipelines.certified_research.contracts import digest
from pipelines.fundamentals.retry import select


def test_retained_registration_gap_requires_exact_source_and_population():
    from pipelines.fundamentals.retry import retained_gaps
    row=dict(cik='0000000001',accession='0000000001-19-000001',document='registration.htm',
             day='2019-01-02',form='F-1/A',content_sha256='a'*64,source_version_key='')
    body=dict(version='retained-financial-gap-v1',population_hash='p',start='2019-01-01',end='2019-12-31',
              reason='retained_structured_values_missing',documents=[row])
    manifest=dict(body,manifest_hash=digest(body))
    args=dict(population_hash='p',ciks=[row['cik']],start=date(2019,1,1),end=date(2019,12,31))
    assert retained_gaps(manifest,lambda sql:[row],**args)==[row]
    for response in ([],[dict(row,content_sha256='b'*64)],[row,row]):
        with pytest.raises(ValueError):retained_gaps(manifest,lambda sql:response,**args)
    with pytest.raises(ValueError):retained_gaps(manifest,lambda sql:[row],**dict(args,ciks=[]))
    changed=dict(body,documents=[row,row]);changed['manifest_hash']=digest(changed)
    with pytest.raises(ValueError):retained_gaps(changed,lambda sql:[row],**args)


@pytest.mark.parametrize('change', [None, 'value', 'span', 'text', 'bytes', 'occurrence'])
def test_explicit_html_variants_require_complete_semantic_equivalence(change):
    from hashlib import sha256
    from pipelines.fundamentals.retry import retained_gaps
    first='<html><p>Issuer USD</p><table><tr><td>Assets</td><td>123</td></tr></table></html>'
    second=first.replace('Assets', ' Assets ')
    if change=='value':second=second.replace('123','124')
    if change=='span':second=second.replace('<td>123','<td rowspan="2">123')
    if change=='text':second=second.replace('Issuer','Other issuer')
    hashes=[sha256(x.encode()).hexdigest() for x in (first,second)]
    row=dict(cik='0000000001',accession='0000000001-19-000001',document='registration.htm',
             day='2019-01-02',form='F-1/A',content_sha256=hashes[0],source_version_key='')
    other=dict(row,day='2019-01-03',content_sha256=hashes[1]);occurrences=[row,other]
    body=dict(version='retained-financial-gap-v1',population_hash='p',start='2019-01-01',end='2019-12-31',
              reason='retained_structured_values_missing',documents=[row],equivalent_occurrences={digest(row):occurrences})
    manifest=dict(body,manifest_hash=digest(body))
    def query(sql):
        if 'SELECT DISTINCT source_text' in sql:
            text=first if hashes[0] in sql else second
            return [dict(source_text=text+'x' if change=='bytes' else text)]
        return occurrences+[dict(other,day='2019-01-04')] if change=='occurrence' else occurrences
    args=dict(population_hash='p',ciks=[row['cik']],start=date(2019,1,1),end=date(2019,12,31))
    if change is None:assert retained_gaps(manifest,query,**args)==[row]
    else:
        with pytest.raises(ValueError):retained_gaps(manifest,query,**args)


def fixture(tmp_path):
    source=tmp_path/'source';source.mkdir();(source/'blocked').mkdir();(source/'receipts').mkdir()
    row=dict(cik='0000000001',accession='0000000001-19-000001',document='a.xml',day='2019-01-02')
    request=dict(population_hash='p',start='2019-01-01',end='2019-12-31')
    (source/'request.json').write_text(json.dumps(request))
    (source/'census.json').write_text(json.dumps(dict(documents=[row],census_hash=digest([row]))))
    (source/'blocked'/(digest(row)+'.json')).write_text(json.dumps(dict(document=row,error='parser')))
    m=dict(version='qualified-blocked-retry-v1',source_run_dir=str(source),source_request_hash=digest(request),
           source_census_hash=digest([row]),documents=[row]);m['manifest_hash']=digest(m)
    args=dict(population_hash='p',start=date(2019,1,1),end=date(2019,12,31),runtime=tmp_path)
    return source,row,m,args


def test_only_failed_targets_and_completed_receipts_are_untouched(tmp_path):
    source,row,m,args=fixture(tmp_path)
    assert select(m,**args)==([row],[])
    record=dict(document=row,publication=dict(state='published'),reuse_verified=True)
    receipt=source/'receipts'/(digest(row)+'.json');receipt.write_text(json.dumps(record));before=receipt.read_bytes()
    selected,done=select(m,**args)
    assert not selected and done[0]['receipt_hash']==digest(record) and receipt.read_bytes()==before


def test_actual_gap_accepts_only_sealed_uncompleted_census_target(tmp_path):
    source,row,m,args=fixture(tmp_path)
    (source/'blocked'/(digest(row)+'.json')).unlink()
    with pytest.raises(FileNotFoundError):select(m,**args)
    m.pop('manifest_hash');m.update(version='qualified-actual-gap-v1',reason='retained_structured_values_missing')
    m['manifest_hash']=digest(m)
    with pytest.raises(ValueError):select(m,**args)
    assert select(m,actual_gaps=True,**args)==([row],[])
    (source/'receipts'/(digest(row)+'.json')).write_text(json.dumps(dict(document=row,publication=dict(state='published'),reuse_verified=True)))
    assert select(m,actual_gaps=True,**args)[0]==[]
    m.pop('manifest_hash');m['documents']=[dict(row,document='unreviewed.xml')];m['manifest_hash']=digest(m)
    with pytest.raises(ValueError):select(m,actual_gaps=True,**args)


@pytest.mark.parametrize('changed',['manifest','request','census','blocked','receipt'])
def test_retry_rejects_changed_identity(tmp_path,changed):
    source,row,m,args=fixture(tmp_path)
    if changed=='manifest':m['documents']=[]
    elif changed in ('request','census'):(source/(changed+'.json')).write_text('{}')
    elif changed=='blocked':(source/'blocked'/(digest(row)+'.json')).write_text('{"document":{}}')
    else:(source/'receipts'/(digest(row)+'.json')).write_text(json.dumps(dict(document=row,publication=dict(state='published'),reuse_verified=False)))
    with pytest.raises((ValueError,KeyError)):select(m,**args)


def test_pid_reuse_does_not_count_as_original_owner(monkeypatch):
    from scripts import retry_qualified_fundamentals as runner
    class Process:
        def create_time(self):return 20
    monkeypatch.setattr(runner.psutil,'Process',lambda _:Process())
    assert runner.owner_alive(dict(pid=123,created=20))
    assert not runner.owner_alive(dict(pid=123,created=10))


def test_failed_or_stale_source_cannot_launch_retry(tmp_path,monkeypatch):
    from scripts import retry_qualified_fundamentals as runner
    monkeypatch.setattr(runner,'owner_alive',lambda _:False)
    (tmp_path/'completion.json').write_text('{"full_scope":true}')
    state=dict(state='failed',updated_at='2026-01-01T00:00:00+00:00',counts=dict(failed=1),active={},queued=0)
    (tmp_path/'status.json').write_text(json.dumps(state))
    with pytest.raises(ValueError):runner.require_drained(tmp_path,[dict(pid=1,created=1)])


def test_refresh_includes_only_current_blocked_census_and_excludes_receipts(tmp_path):
    from pipelines.fundamentals.retry import refresh
    source,row,m,args=fixture(tmp_path)
    extra=dict(row,document='new.xml');census=[row,extra]
    (source/'census.json').write_text(json.dumps(dict(documents=census,census_hash=digest(census))))
    m.pop('manifest_hash');m['source_census_hash']=digest(census);m['manifest_hash']=digest(m)
    (source/'blocked'/(digest(extra)+'.json')).write_text(json.dumps(dict(document=extra,error='new blocker')))
    changed=refresh(m);assert changed['documents']==census
    (source/'receipts'/(digest(row)+'.json')).write_text(json.dumps(dict(document=row,publication=dict(state='published'),reuse_verified=True)))
    changed=refresh(m);assert changed['documents']==[extra]
    assert select(changed,**args)==([extra],[])
