from datetime import date

import pytest
from unittest.mock import Mock,patch
from types import SimpleNamespace
from threading import Event
from pipelines.certified_research.sec_filing_census import inventory,reconcile
from scripts.prepare_certified_research import parse_args,prepare_sec_share_sources


def query_for(rows):
    def query(sql):
        assert sql.startswith('SELECT ')
        if 'count()' in sql:return [dict(n=len(rows),bytes=100,source_hash='a'*64)]
        return rows
    return query


def row(**changes):return dict(dict(cik='0000320193',accession_number='0000320193-25-000079',form_type='10-K',
    filing_date='2025-10-31',primary_document='aapl.htm',source_content_sha256='b'*64),**changes)


def test_independent_submission_reconciliation_retains_missing_and_conflicting_evidence():
    def census(rows,source):
        return inventory(query_for(rows),('320193',),date(2025,1,1),date(2025,12,31),forms=('10-K',),source=source)
    retained=census([row()],'retained')
    submission_row=row(source_kind='submissions_bulk',source_file_id='original-source')
    matched=reconcile(retained,census([submission_row],'bulk_submissions'))
    assert matched['identity_reconciled'] and not matched['coverage_certified']
    assert reconcile(retained,census([],'bulk_submissions'))['unconfirmed_retained']
    assert reconcile(census([],'retained'),census([submission_row],'bulk_submissions'))['missing_retained']
    changed=dict(submission_row,primary_document='different.htm')
    assert reconcile(retained,census([changed],'bulk_submissions'))['conflicting_metadata']
    with pytest.raises(ValueError,match='fingerprint'):
        reconcile(dict(retained,filings=99),census([submission_row],'bulk_submissions'))


def test_census_preserves_unknown_dates_and_missing_primary_documents_as_blockers():
    result=inventory(query_for([row(filing_date=None,primary_document=None)]),('320193',),date(2025,1,1),date(2025,12,31),forms=('10-K',))
    assert not result['discovery_ready'] and not result['coverage_certified']
    assert result['issue_counts']=={'missing_primary_document':1,'unknown_filing_date':1}


def test_unique_qualified_metadata_is_discovery_only():
    result=inventory(query_for([row()]),('320193',),date(2025,1,1),date(2025,12,31),forms=('10-K',))
    assert result['discovery_ready'] and len(result['documents'])==1 and not result['coverage_certified']
    with pytest.raises(ValueError,match='duplicate accession'):
        inventory(query_for([row(),row()]),('320193',),date(2025,1,1),date(2025,12,31),forms=('10-K',))


@pytest.mark.parametrize('source',['retained','bulk_submissions'])
def test_census_pushes_only_replacement_key_before_final(source):
    statements=[]
    sample=row()
    if source=='bulk_submissions':sample.update(source_kind='submissions_bulk',source_file_id='original-source')
    delegate=query_for([sample])
    def query(sql):
        statements.append(sql)
        return delegate(sql)
    inventory(query,('320193',),date(2025,1,1),date(2025,12,31),forms=('10-K',),source=source)
    assert len(statements)==3
    for sql in statements:
        assert "FINAL PREWHERE cik IN ('0000320193','320193') WHERE form_type IN ('10-K')" in sql
        assert 'filing_date BETWEEN' in sql.split(' WHERE ',1)[1]


def test_launcher_census_selects_documents_before_preparation():
    base=['prepare-sources','--end-date','2026-01-02','--capacity-profile','pilot.json','--fundamental-start-date','2025-01-01',
        '--sec-census-cik','320193','--sec-census-forms','10-K,10-Q']
    args=parse_args(base)
    discovered=inventory(query_for([row()]),('320193',),date(2025,1,1),date(2026,1,2),forms=('10-K','10-Q'))
    with patch('pipelines.certified_research.sec_filing_census.inventory',return_value=discovered) as census,\
         patch('pipelines.certified_research.capacity_profile.load',return_value={}),\
         patch('pipelines.certified_research.sec_share_preparation.prepare',return_value=dict(status='complete',captured=1,reused=0)) as prepare:
        result=prepare_sec_share_sources(args,Mock(),SimpleNamespace(end=date(2026,1,2)),Mock(),Mock(),Mock(),Event())
    assert prepare.call_args.args[2]==[('320193','0000320193-25-000079','aapl.htm')]
    assert result['filing_census']['coverage_certified'] is False
    assert census.call_count==2
    with pytest.raises(SystemExit):parse_args(base+['--sec-share-document','320193,0000320193-25-000079,aapl.htm'])
    args=parse_args(base)
    with patch('pipelines.certified_research.sec_filing_census.inventory',side_effect=[discovered,dict(discovered,census_hash='c'*64)]),\
         patch('pipelines.certified_research.capacity_profile.load',return_value={}),\
         patch('pipelines.certified_research.sec_share_preparation.prepare',return_value=dict(status='complete',captured=1,reused=0)),\
         pytest.raises(ValueError,match='census changed'):
        prepare_sec_share_sources(args,Mock(),SimpleNamespace(end=date(2026,1,2)),Mock(),Mock(),Mock(),Event())


def test_launcher_submission_mismatch_blocks_before_any_preparation():
    base=['prepare-sources','--end-date','2026-01-02','--capacity-profile','pilot.json','--fundamental-start-date','2025-01-01',
        '--sec-census-cik','320193','--sec-census-forms','10-K','--reconcile-sec-submissions']
    args=parse_args(base)
    retained=inventory(query_for([row()]),('320193',),date(2025,1,1),date(2026,1,2),forms=('10-K',))
    independent=inventory(query_for([]),('320193',),date(2025,1,1),date(2026,1,2),forms=('10-K',),source='bulk_submissions')
    with patch('pipelines.certified_research.sec_filing_census.inventory',side_effect=[retained,independent]),\
         patch('pipelines.certified_research.sec_submission_coverage.audit',return_value=dict(retained_evidence_ready=False)),\
         patch('pipelines.certified_research.sec_share_preparation.prepare') as prepare,\
         pytest.raises(ValueError,match='identities differ'):
        prepare_sec_share_sources(args,Mock(),SimpleNamespace(end=date(2026,1,2)),Mock(),Mock(),Mock(),Event())
    prepare.assert_not_called()
    with pytest.raises(SystemExit):parse_args(['prepare-sources','--reconcile-sec-submissions'])
