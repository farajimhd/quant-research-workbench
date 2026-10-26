from datetime import datetime,timezone
from hashlib import sha256
import json
from types import SimpleNamespace
import pytest

from tests.test_certified_sec_share_context import document
from pipelines.sec.edgar.sec_pipeline import original_facts as original
from pipelines.sec.edgar.sec_pipeline.public_time import historical_time
from pipelines.sec.edgar.sec_pipeline.live_pipeline import SecLiveFilingPipeline
from pipelines.sec.edgar.sec_pipeline.xbrl_live import LiveXbrlRows,extract_companyfacts_payload
from pipelines.sec.edgar.sec_pipeline.feed import SecFeedItem
from pipelines.sec.edgar.sec_pipeline.http import SecHttpResponse

CIK='0000320193'
ACCESSION='0000320193-25-000001'


def financial(value='123000'):
    return document(value).replace('dei:EntityCommonStockSharesOutstanding','g:Revenues').replace(
        'xmlns:g=','xmlns:iso="http://www.xbrl.org/2003/iso4217" xmlns:g=').replace('x:shares','iso:USD').replace(
        '<x:instant>2025-10-17</x:instant>','<x:startDate>2025-01-01</x:startDate><x:endDate>2025-09-30</x:endDate>')


def metadata():
    return dict(acceptance_datetime_raw='20251017153000',filing_date='2025-10-17',public_document_count=1,
        form_type='10-Q',source_version_key='parent-version',source_content_sha256='a'*64)


def extract(payload,root):
    return original.extract_accession([dict(document_name='report.htm',source_text=payload,
        content_sha256=sha256(payload.encode()).hexdigest(),document_role='primary_filing')],metadata(),
        cik=CIK,accession=ACCESSION,inserted_at='2026-10-26T12:00:00.000Z',source_run_id='test',cache_root=root)


def test_exact_duration_cache_and_two_clocks(tmp_path,monkeypatch):
    payload=financial('1234567890123456789000')
    rows,outcomes=extract(payload,tmp_path)
    assert outcomes[0]['status']=='extracted'
    row=rows[0]
    assert (row['period_start_date'],row['period_end_date'],row['unit_code'])==('2025-01-01','2025-09-30','USD')
    assert row['value_exact']=='1234567890123456789000'
    assert row['public_available_at']=='2025-10-17 19:30:00.000000'
    assert row['recorded_at_utc']=='2026-10-26T12:00:00.000Z'
    monkeypatch.setattr(original.sec_fundamental_context,'extract',lambda *a,**k:pytest.fail('reparsed cached document'))
    assert extract(payload,tmp_path)==(rows,outcomes)
    cached=next(tmp_path.glob('*.json'));body=json.loads(cached.read_text());body['result']['groups'][0]['value']='0'
    cached.write_text(json.dumps(body))
    assert extract(payload,tmp_path)[1][0]['status']=='review_required'


def test_conflicting_and_nil_facts_are_not_zeroed(tmp_path):
    bad=financial().replace('<x:startDate>2025-01-01','<x:startDate>2026-01-01')
    rows,outcomes=extract(bad,tmp_path)
    assert rows==[] and outcomes[0]['status']=='review_required'
    rows,outcomes=extract('<html>Unmapped narrative financial statements.</html>',tmp_path)
    assert rows==[] and outcomes[0]['status']=='untagged_review_required'
    with pytest.raises(ValueError,match='hash'):
        original.extract_document(financial(),'0'*64,CIK)


def test_shared_time_rejects_special_events_and_ambiguous_clock():
    assert historical_time(metadata())['availability_basis']=='sec_acceptance_proxy'
    with pytest.raises(ValueError,match='separate review'):
        historical_time(dict(metadata(),private_to_public=1))
    with pytest.raises(ValueError,match='timezone'):
        historical_time(dict(metadata(),acceptance_datetime_raw='20251102013000',filing_date='2025-11-02'))


def test_submissions_clock_fallback_is_bound_to_same_parent():
    from pipelines.sec.edgar.sec_pipeline.public_time import source_metadata
    archive=dict(metadata(),acceptance_datetime_raw='',primary_cik=CIK,accession_number=ACCESSION)
    filing=dict(cik=CIK,accession_number=ACCESSION,form_type='10-Q',filing_date='2025-10-17',
        accepted_at_source='submissions_recent',acceptance_datetime_raw='2025-10-17T19:30:00.000Z')
    assert historical_time(source_metadata(archive,filing))['available_at']=='2025-10-17 19:30:00.000000'
    assert source_metadata(archive,dict(filing,cik='0000000001'))==archive
    assert source_metadata(archive,dict(filing,accepted_at_source='filing_date_fallback'))==archive


def test_source_period_and_frame_are_preserved():
    payload={'facts':{'us-gaap':{'Revenues':{'units':{'USD':[dict(accn=ACCESSION,val=123,
        start='2025-01-01',end='2025-09-30',filed='2025-10-17',frame='CY2025Q3',form='10-Q')]}}}}}
    rows=extract_companyfacts_payload(payload,cik=CIK,accession_numbers={ACCESSION},source_run_id='test',
        inserted_at='2026-10-26T12:00:00Z',source_content_sha256='a'*64)
    assert rows.company_fact_rows[0]['period_start_date']=='2025-01-01'
    assert rows.company_fact_rows[0]['frame']=='CY2025Q3'
    assert rows.company_fact_rows[0]['value_exact']=='123'


def test_real_feed_pipeline_preserves_receipt_and_extracts_without_extra_download(tmp_path,monkeypatch):
    payload=financial()
    raw=f'''<SEC-DOCUMENT>{ACCESSION}.txt : 20251017
<SEC-HEADER>
ACCESSION NUMBER: {ACCESSION}
CONFORMED SUBMISSION TYPE: 10-Q
PUBLIC DOCUMENT COUNT: 1
FILED AS OF DATE: 20251017
<ACCEPTANCE-DATETIME>20251017153000
FILER:
 COMPANY DATA:
  COMPANY CONFORMED NAME: TEST CO
  CENTRAL INDEX KEY: {CIK}
</SEC-HEADER>
<DOCUMENT>
<TYPE>10-Q
<SEQUENCE>1
<FILENAME>report.htm
<TEXT>
{payload}
</TEXT>
</DOCUMENT>
</SEC-DOCUMENT>'''.encode()
    calls=[]
    def get(url):
        calls.append(url);return SecHttpResponse(url=url,status=200,content_type='text/plain',body=raw)
    pipeline=SecLiveFilingPipeline(http=SimpleNamespace(get=get),raw_root_win=tmp_path/'raw')
    monkeypatch.setattr(pipeline.submissions,'fetch_recent_filing',lambda **k:None)
    monkeypatch.setattr(pipeline.xbrl_extractor,'extract_for_accession',lambda **k:LiveXbrlRows())
    monkeypatch.setenv('SEC_ORIGINAL_CACHE_ROOT',str(tmp_path/'cache'))
    item=SecFeedItem(accession_number=ACCESSION,cik=CIK,form_type='10-Q',title='TEST',
        updated_at_utc=datetime(2025,10,17,19,30,tzinfo=timezone.utc),filing_detail_url='',
        accession_number_compact=ACCESSION.replace('-',''),primary_document_url='')
    result=pipeline.process_feed_item(item,source_run_id='test')
    assert len(calls)==1
    assert result.xbrl_rows.original_fact_rows and not result.xbrl_rows.company_fact_rows
    receipt=json.loads(result.raw_path.with_suffix('.receipt.json').read_text())
    assert receipt['content_sha256']==sha256(raw).hexdigest()
    assert result.archive_accession_rows[0]['download_receipt_sha256']
    old=result.raw_path.read_bytes()
    changed=pipeline.write_raw(item,raw+b'\n')
    assert changed!=result.raw_path and result.raw_path.read_bytes()==old


def test_original_readback_rejects_altered_context():
    rows,_=extract(financial(),None)
    columns=['company_fact_id','source_content_sha256','document_name','value_exact','period_start_date',
        'period_end_date','dimensions_json','source_positions','extraction_version','source_kind',
        'availability_basis','public_available_at','source_version_key','source_accession_sha256','time_evidence_json']
    actual={k:rows[0][k] for k in columns}
    client=SimpleNamespace(execute=lambda sql:json.dumps(actual))
    original.verify_readback(client,'test',rows)
    actual['period_start_date']='2025-07-01'
    with pytest.raises(ValueError,match='readback'):
        original.verify_readback(client,'test',rows)


def test_live_clickhouse_source_normalization_and_reuse(monkeypatch,tmp_path):
    import os,uuid,time
    if os.environ.get('SEC_FUNDAMENTALS_LIVE_TEST')!='1':pytest.skip('explicit isolated ClickHouse check')
    from research.mlops.env import load_env_files
    from research.mlops.clickhouse import discover_clickhouse_env_files
    from scripts.run_fundamentals import Client
    from pipelines.sec.edgar.sec_pipeline.clickhouse_writer import (ensure_sec_write_database,
        SecClickHouseWriter,verify_sec_write_storage)
    from pipelines.fundamentals import schema,producer,adapters
    from dataclasses import replace
    from datetime import date
    from pipelines.certified_research.contracts import digest
    load_env_files(discover_clickhouse_env_files(),verbose=False)
    client=Client('http://192.168.1.218:18123');db='fundamentals_validation_gateway_'+uuid.uuid4().hex[:10]
    started=time.perf_counter()
    try:
        ensure_sec_write_database(client.http,read_database='q_live',write_database=db)
        writer=SecClickHouseWriter(client.http,database=db);writer.validate_tables()
        from pipelines.sec.edgar.sec_bulk_to_canonical import xbrl_company_fact_insert_sql
        bulk=xbrl_company_fact_insert_sql(SimpleNamespace(source_database='sec_core',target_database=db,
            start_date='2026-10-26',end_date='2026-10-27'),'schema-compatibility-test')
        # The server supports EXPLAIN only for SELECT. A zero-row INSERT
        # validates the real target column mapping without scanning history.
        client.query(bulk+' AND 0',read=False)
        rows,outcomes=extract(financial('1234567890123456789000'),tmp_path/'extract')
        writer.insert_rows(original.TABLE,rows)
        original.verify_readback(client.http,db,rows)
        retry=[dict(r,recorded_at_utc='2026-10-27T12:00:00Z') for r in rows]
        assert original.missing_rows(client.http,db,retry)==[]
        verify_sec_write_storage(client.http,db,[original.TABLE])
        schema.install(client.query,db)
        monkeypatch.setitem(adapters.ADAPTERS,'sec_ingested_original',replace(adapters.ADAPTERS['sec_ingested_original'],
            table=db+'.'+original.TABLE))
        part=client.query(f"SELECT DISTINCT partition_id FROM system.parts WHERE active AND database='{db}' AND table='{original.TABLE}'")[0]['partition_id']
        batch=producer.Batch('sec_ingested_original',part,(CIK,),date(1900,1,1),date(2140,1,1),digest('gateway-test'),selected=True)
        first=producer.publish(client.query,batch,db);second=producer.publish(client.query,batch,db)
        assert first['state']=='published' and second['state']=='unchanged'
        facts=client.query(f"SELECT text_value FROM {db}.reference_fact_v1 FINAL")
        assert facts==[dict(text_value='1234567890123456789000')]
        context=client.query(f"SELECT toString(period_start) start,toString(period_end) end,unit FROM {db}.reference_context_v1 FINAL")
        assert context==[dict(start='2025-01-01',end='2025-09-30',unit='USD')]
        (tmp_path/'profile.json').write_text(json.dumps(dict(first=first,repeat=second,seconds=time.perf_counter()-started)))
    finally:
        client.query(f'DROP DATABASE IF EXISTS {db} SYNC',read=False);client.close()
