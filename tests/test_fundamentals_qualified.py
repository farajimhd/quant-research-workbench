from datetime import datetime,timezone,timedelta
from unittest.mock import Mock
import pytest
from pipelines.fundamentals import qualified,progress


def test_cross_campaign_reuse_preserves_authority_observation_time(monkeypatch):
    verified=dict(authority_hash='a'*64,cik='0000320193',available_at='2025-11-01 04:00:00',
        groups=[dict(concept='{http://fasb.org/us-gaap/2025}Revenues',period_start='2025-01-01',
            period_end='2025-09-30',dimensions=(),unit='USD',value='12345678901234567890.123')])
    monkeypatch.setattr(qualified.schema,'verify',lambda *a:None)
    monkeypatch.setattr(qualified.sec_original_authority,'verify_document',lambda *a:verified)
    monkeypatch.setattr(qualified,'_different',lambda *a:None)
    stored={};inserted=[]
    def insert(query,database,table,rows,**kwargs):
        inserted.extend((table,r) for r in rows)
        if table=='reference_batch_receipt_v1':stored['receipt']=rows[0]
    monkeypatch.setattr(qualified,'insert_rows',insert)
    stamp=datetime(2026,10,23,tzinfo=timezone.utc)
    def query(sql,**kwargs):
        if sql.startswith('SELECT DISTINCT toString(observed_at)'):
            return [dict(observed_at=stamp.strftime('%Y-%m-%d %H:%M:%S.%f'),recipe_hash=stored['receipt']['recipe_hash'])] if 'receipt' in stored else []
        if sql.startswith('SELECT fact_rows,output_hash'):
            return [stored['receipt']] if 'receipt' in stored else []
        return []
    args=(query,'q_live','arte',('0000320193','accession','document'),'b'*64,'c'*64)
    first=qualified.publish(*args,stamp,Mock(),lambda:None)
    before=len(inserted)
    second=qualified.publish(*args,stamp+timedelta(days=1),Mock(),lambda:None)
    assert first['output_hash']==second['output_hash'] and second['state']=='reused'
    assert len(inserted)==before
    fact=next(r for table,r in inserted if table=='reference_stage_v1')
    assert fact['value'] is None and fact['text_value']=='12345678901234567890.123'
    stored['receipt']['output_hash']='bad'
    with pytest.raises(ValueError,match='receipt differs'):qualified.publish(*args,stamp,Mock(),lambda:None)


def test_qualified_progress_has_truthful_units_and_no_premature_eta():
    state=dict(state='running',unit='documents',queued=3,total=3,counts={},active={},
        qualification_status='Qualified originals; unresolved coverage blocked')
    assert progress.estimate(state) is None
    rendered=progress.render(state,120,30)
    assert '0/3 documents' in rendered and 'ETA estimating' in rendered
    assert 'Qualified originals' in rendered


def test_native_publication_requires_exact_definitions_and_reuses_dictionary(monkeypatch):
    from pipelines.certified_research.sec_html_native import definition
    native=definition('320193','Adjusted operating result','Consolidated operating measures')
    verified=dict(authority_hash='a'*64,cik='0000320193',available_at='2025-11-01 04:00:00',
        native_definitions={native['concept']:native},groups=[dict(concept=native['concept'],
        period_start='2025-01-01',period_end='2025-09-30',dimensions=(),unit='USD',value='123')])
    monkeypatch.setattr(qualified.schema,'verify',lambda *a:None)
    monkeypatch.setattr(qualified.sec_original_authority,'verify_document',lambda *a:verified)
    monkeypatch.setattr(qualified,'_different',lambda *a:None)
    stored={};definitions=[];writes=[]
    def insert(query,database,table,rows,**kwargs):
        writes.append(table)
        if table=='reference_metric_definition_v1':definitions.extend(rows)
        if table=='reference_batch_receipt_v1':stored['receipt']=rows[0]
    monkeypatch.setattr(qualified,'insert_rows',insert)
    def query(sql,**kwargs):
        if 'SELECT metric,issuer_cik,label,statement_scope,definition_hash' in sql:return definitions
        if sql.startswith('SELECT fact_rows,output_hash'):return [stored['receipt']] if stored else []
        return []
    args=(query,'q_live','arte',('0000320193','accession','document'),'b'*64,'c'*64,
        datetime(2026,10,23,tzinfo=timezone.utc),Mock(),lambda:None)
    qualified.publish(*args)
    assert definitions[0]['label']=='Adjusted operating result'
    count=len(writes)
    assert qualified.publish(*args)['state']=='reused' and len(writes)==count
    definitions[0]['label']='Operating income'
    with pytest.raises(ValueError,match='conflicts'):qualified.publish(*args)
    verified['native_definitions']={}
    with pytest.raises(ValueError,match='definitions differ'):qualified.publish(*args)


def test_eta_includes_serial_archive_work_and_does_not_hide_unsampled_stages():
    state=dict(state='running',workers=4,queued=100,remaining_by_family={'SEC':100},
        samples={'SEC':{'published':[8,8,8]}},archive_remaining=10,archive_samples=[20,20])
    assert progress.estimate(state) is None
    state['archive_samples'].append(20)
    assert progress.estimate(state)==400
