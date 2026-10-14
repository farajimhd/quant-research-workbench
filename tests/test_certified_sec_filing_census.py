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
