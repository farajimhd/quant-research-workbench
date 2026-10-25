import hashlib
import json
from datetime import datetime, timezone
import pytest
from pipelines.fundamentals.period_recovery import recovery_sql, classification_sql
from pipelines.fundamentals.v1 import financial_sql
from pipelines.certified_research.contracts import literal
from tests.test_fundamentals_retained import live


def test_serving_keeps_receipts_and_conservative_clocks():
    sql=financial_sql(['0000320193'],datetime(2026,10,24,tzinfo=timezone.utc))
    assert "'sec_reported'" in sql and 'source_content_sha256' in sql
    assert 'JSONExtractKeys' in sql and 'America/New_York' in sql
    assert 'uniqExact(tuple(period_start,frame))=1' in sql
    assert 'period_end<=toDate' in sql
    with pytest.raises(ValueError):recovery_sql('SELECT 1',maximum_days=732)


@pytest.mark.parametrize('unit',['USD','USD/shares'])
def test_live_exact_period_identity_recovery(live,unit):
    q,db,_,_=live
    parts=['sec-xbrl-company-fact','0000320193','us-gaap','Revenues',unit,
           '2026-03-29','2026-06-27','0000320193-26-000020','CY2026Q2']
    identity=hashlib.sha256(json.dumps(parts,separators=(',',':')).encode()).hexdigest()
    candidate=f'''SELECT {literal(identity)} company_fact_id,'source' source_hash,
      '0000320193' cik,'us-gaap' taxonomy,'Revenues' tag,{literal(unit)} unit_code,
      toDate('2026-06-27') period_end_date,'0000320193-26-000020' accession_number'''
    rows=q(recovery_sql(candidate))
    assert len(rows)==1 and rows[0]['period_start']=='2026-03-29'
    assert rows[0]['recovered_frame']=='CY2026Q2'
    assert q(recovery_sql(candidate.replace(identity,'0'*64)))==[]
    assert q(recovery_sql(candidate,maximum_days=30))==[]
    q(f'INSERT INTO {db}.reference_period_evidence_v1 '+recovery_sql(candidate),read=False)
    assert q(f'SELECT period_start,frame FROM {db}.reference_period_evidence_v1 FINAL')==[
        {'period_start':'2026-03-29','frame':'CY2026Q2'}]


def test_live_empty_source_start_is_classified_without_inventing_duration(live):
    q,_,_,_=live
    parts=['sec-xbrl-company-fact','0000320193','us-gaap','Revenues','USD',
           '','2026-06-27','0000320193-26-000020','CY2026Q2I']
    identity=hashlib.sha256(json.dumps(parts,separators=(',',':')).encode()).hexdigest()
    candidate=f'''SELECT {literal(identity)} company_fact_id,'source' source_hash,
      '0000320193' cik,'us-gaap' taxonomy,'Revenues' tag,'USD' unit_code,
      toDate('2026-06-27') period_end_date,'0000320193-26-000020' accession_number'''
    assert q('SELECT source_instant_context FROM ('+classification_sql(candidate)+')')==[
        {'source_instant_context':1}]
    assert q(recovery_sql(candidate))==[]
    assert q('SELECT source_instant_context FROM ('+classification_sql(candidate.replace(identity,'0'*64))+')')==[
        {'source_instant_context':0}]
