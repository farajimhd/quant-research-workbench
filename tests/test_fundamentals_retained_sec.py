from datetime import datetime, timezone
import pytest
from pipelines.fundamentals.retained_sec import historical_time, inspect


def metadata(**updates):
    return dict(filing_date='2026-08-25', acceptance_datetime_raw='20260825120000',
                private_to_public=0, pac_event_id=None, public_document_count=5, **updates)


def test_historical_clock_is_not_the_fallback_ingestion_clock():
    result = historical_time(metadata())
    assert result['available_at'] == '2026-08-25 16:00:00.000000'
    assert result['availability_basis'] == 'sec_acceptance_proxy'
    assert result['exact_publication_time'] is False
    assert result['recorded_time_basis'] == 'ingestion_time_fallback_no_download_receipt'
    assert '2026-10-25' not in str(result)


def test_later_filing_day_is_not_backdated_to_late_acceptance():
    row=metadata(); row.update(filing_date='2026-08-26',acceptance_datetime_raw='20260825180000')
    assert historical_time(row)['available_at']=='2026-08-26 04:00:00.000000'
    assert historical_time(row)['availability_basis']=='sec_later_filing_day'


def test_retained_submissions_timestamp_preserves_utc():
    row=metadata();row.update(acceptance_datetime_raw='2026-08-25T16:00:00.000Z',time_metadata_source='sec_filing_v3')
    assert historical_time(row)['available_at']=='2026-08-25 16:00:00.000000'
    del row['time_metadata_source']
    with pytest.raises(ValueError):historical_time(row)


@pytest.mark.parametrize('updates',[
    {'acceptance_datetime_raw':None}, {'private_to_public':1}, {'pac_event_id':'correction'},
    {'public_document_count':0}, {'filing_date':'2026-08-24'},
    {'filing_date':'2026-11-01','acceptance_datetime_raw':'20261101013000'},
    {'filing_date':'2026-03-08','acceptance_datetime_raw':'20260308023000'},
])
def test_ambiguous_or_special_times_still_block(updates):
    row=metadata(); row.update(updates)
    with pytest.raises(ValueError):historical_time(row)


def test_competing_payload_versions_are_not_hidden_by_expected_hash():
    doc=dict(cik='0001903784',accession='0001185185-26-004101',document='a.htm',content_sha256='a'*64)
    def query(sql):
        assert "content_sha256='" not in sql
        return [dict(content_sha256='a'*64),dict(content_sha256='b'*64)]
    with pytest.raises(ValueError,match='competing'):inspect(query,doc)


@pytest.mark.parametrize('wrong_member',[False,True])
def test_exhibit_requires_exact_versioned_parent_membership(wrong_member):
    doc=dict(cik='0002041208',accession='0001213900-26-088473',document='ex15.htm',content_sha256='a'*64)
    source=dict(cik=doc['cik'],accession_number=doc['accession'],document_name=doc['document'],
                content_sha256='a'*64,source_version_key='b'*64,document_type='EX-15.6',source_archive_date='2026-08-25')
    def query(sql):
        if 'sec_filing_text_v3' in sql:return [dict(source,bytes=100)]
        if 'sec_filing_document_v3' in sql:return [dict(source,content_sha256='c'*64 if wrong_member else 'a'*64)]
        return [dict(metadata(),primary_cik=doc['cik'],form_type='20-F',source_version_key='b'*64)]
    if wrong_member:
        with pytest.raises(ValueError,match='membership'):inspect(query,doc)
    else:
        result=inspect(query,doc)
        assert result['metadata']['exhibit_member']==source
        assert result['time_evidence']['available_at']=='2026-08-25 16:00:00.000000'


@pytest.mark.parametrize('wrong', [False, True])
def test_legacy_time_requires_matching_primary_document(wrong):
    doc=dict(cik='0001849288',accession='0001104659-21-064667',document='a.htm',content_sha256='a'*64)
    def query(sql):
        if 'sec_filing_text_v3' in sql:
            return [dict(content_sha256='a'*64,bytes=100,source_version_key='',document_type='S-1')]
        if 'sec_filing_archive_accession_v3' in sql:
            return [dict(primary_cik=doc['cik'],form_type='S-1',filing_date='2021-05-11',acceptance_datetime_raw=None,
                         public_document_count=1,private_to_public=0,pac_event_id=None)]
        return [dict(cik=doc['cik'],form_type='S-1',filing_date='2021-05-11',primary_document='other.htm' if wrong else 'a.htm',
                     acceptance_datetime_raw='2021-05-11T17:31:20.000Z',source_content_sha256='b'*64)]
    if wrong:
        with pytest.raises(ValueError,match='Legacy'):inspect(query,doc)
    else:
        assert inspect(query,doc)['time_evidence']['available_at']=='2021-05-11 17:31:20.000000'
