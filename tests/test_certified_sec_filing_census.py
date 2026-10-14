from datetime import date

import pytest
from pipelines.certified_research.sec_filing_census import inventory


def query_for(rows):
    def query(sql):
        assert sql.startswith('SELECT ')
        if 'count()' in sql:return [dict(n=len(rows),bytes=100,source_hash='a'*64)]
        return rows
    return query


def row(**changes):return dict(dict(cik='0000320193',accession_number='0000320193-25-000079',form_type='10-K',
    filing_date='2025-10-31',primary_document='aapl.htm',source_content_sha256='b'*64),**changes)


def test_census_preserves_unknown_dates_and_missing_primary_documents_as_blockers():
    result=inventory(query_for([row(filing_date=None,primary_document=None)]),('320193',),date(2025,1,1),date(2025,12,31),forms=('10-K',))
    assert not result['discovery_ready'] and not result['coverage_certified']
    assert result['issue_counts']=={'missing_primary_document':1,'unknown_filing_date':1}


def test_unique_qualified_metadata_is_discovery_only():
    result=inventory(query_for([row()]),('320193',),date(2025,1,1),date(2025,12,31),forms=('10-K',))
    assert result['discovery_ready'] and len(result['documents'])==1 and not result['coverage_certified']
    with pytest.raises(ValueError,match='duplicate accession'):
        inventory(query_for([row(),row()]),('320193',),date(2025,1,1),date(2025,12,31),forms=('10-K',))
