from datetime import date

import pytest

from pipelines.certified_research.sec_filing_census import inventory
from pipelines.certified_research.sec_submission_coverage import audit


def census():
    row=dict(cik='0000320193',accession_number='0000320193-25-000079',form_type='10-K',filing_date='2025-10-31',
        primary_document='aapl.htm',source_content_sha256='b'*64,source_kind='submissions_bulk',source_file_id='source')
    return inventory(lambda sql:[dict(n=1,bytes=100,source_hash='a'*64)] if 'count()' in sql else [row],
        ('320193',),date(2025,1,1),date(2025,12,31),forms=('10-K',),source='bulk_submissions')


def evidence(*,modified='2026-01-01 00:30:00',fragment=True,source='source'):
    ref=dict(cik='0000320193',file_name='CIK0000320193-submissions-001.json',filing_count=2,
        filing_from='2024-01-01',filing_to='2025-03-01',source_file_id='source')
    parent=dict(member_name='CIK0000320193.json',cik='0000320193',source_file_id='source',
        member_modified_at_utc=modified,status='completed',member_signature='signature',member_file_size=100)
    members=[parent]
    if fragment:members.append(dict(parent,member_name=ref['file_name'],source_file_id=source))
    def query(sql):
        rows=[ref] if 'submission_file_ref' in sql else members
        return [dict(n=len(rows),bytes=100,source_hash='c'*64)] if 'count()' in sql else rows
    return query


def test_complete_retained_metadata_is_only_diagnostic():
    result=audit(evidence(),census())
    assert result['retained_evidence_ready'] and result['required_fragments']==1
    assert not result['coverage_certified']


@pytest.mark.parametrize('options,reason',[
    (dict(modified='2025-06-01 00:30:00'),'parent_vintage_does_not_cover_end'),
    (dict(fragment=False),'missing_or_ambiguous_member'),
    (dict(source='different'),'missing_or_ambiguous_member'),
])
def test_stale_parent_missing_fragment_and_source_mismatch_block(options,reason):
    result=audit(evidence(**options),census())
    assert not result['retained_evidence_ready']
    assert reason in {item['reason'] for item in result['issues']}


def test_forged_census_and_metadata_drift_fail_closed():
    with pytest.raises(ValueError,match='fingerprint'):audit(evidence(),dict(census(),filings=3))
    query=evidence();calls=0
    def drifting(sql):
        nonlocal calls
        calls+=1
        rows=query(sql)
        if calls==3:rows=[dict(rows[0],source_hash='f'*64)]
        return rows
    with pytest.raises(ValueError,match='changed'):audit(drifting,census())
