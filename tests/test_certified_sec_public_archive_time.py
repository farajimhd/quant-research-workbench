
import pytest

from pipelines.certified_research import sec_public_archive_time as timing
from pipelines.certified_research.contracts import digest


def inputs():
    witness=dict(version='sec-archive-document-byte-witness-v2',original_bytes_verified=True,
        container_digest_verified=True,special_release=False,public_document_count=2,
        source_archive_date='2025-10-31',form_type='10-K',acceptance_datetime_raw='20251031093000',
        official_filing_date='2025-10-31',filing_date_change='2025-10-31',
        container_sha256='a'*64,archive_bytes=1234)
    witness['witness_hash']=digest(witness)
    receipt=dict(status='downloaded',source_kind='daily_feed_archive',
        source_url='https://www.sec.gov/Archives/edgar/Feed/2025/QTR4/20251031.nc.tar.gz',
        source_date='2025-10-31',sha256='a'*64,byte_size=1234,
        last_modified='Sat, 01 Nov 2025 02:10:54 GMT',downloaded_at_utc='2026-06-12T07:46:29.125026Z')
    return witness,receipt


def reseal(witness):
    witness.pop('witness_hash',None);witness['witness_hash']=digest(witness)


def test_conservative_feed_day_is_not_acceptance_or_later_download_time():
    result=timing.qualify(*inputs())
    assert result['available_at']=='2025-11-01T04:00:00+00:00'
    assert result['precision']==2 and result['exact_posting_time'] is False
    assert result['public_time_qualified'] is True and result['allocation_certified'] is False


def test_explicit_download_policy_never_backdates_regenerated_archive():
    w,r=inputs();r['last_modified']='Sat, 01 Nov 2025 21:30:16 GMT'
    with pytest.raises(ValueError):timing.qualify(w,r,mode='official_filing_day')
    result=timing.qualify(w,r,mode='verified_download_time')
    assert result['available_at']=='2026-06-12T07:46:29.125026+00:00'
    assert result['filing_date']=='2025-10-31' and result['availability_basis']=='verified_public_download'
    assert not result['exact_posting_time']
    for changes in (dict(sha256='b'*64),dict(status='reused'),dict(last_modified='Sat, 13 Jun 2026 21:30:16 GMT'),dict(last_modified='Thu, 30 Oct 2025 21:30:16 GMT')):
        with pytest.raises(ValueError):timing.qualify(w,dict(r,**changes),mode='verified_download_time')


@pytest.mark.parametrize('field,value',[
    ('special_release',True),('container_digest_verified',False),('public_document_count',None),
    ('public_document_count',0),('acceptance_datetime_raw',''),('acceptance_datetime_raw','20251031174000'),
    ('acceptance_datetime_raw','20251103093000'),('form_type',''),
])
def test_unknown_special_or_deferred_states_cannot_escape(field,value):
    witness,receipt=inputs();witness[field]=value;reseal(witness)
    with pytest.raises(ValueError):timing.qualify(witness,receipt)


@pytest.mark.parametrize('field,value',[
    ('status','reused'),('sha256','b'*64),('source_date','2025-11-01'),
    ('last_modified','Sat, 01 Nov 2025 07:10:54 GMT'),('source_url','https://example.com/archive'),
])
def test_receipt_and_representation_mismatches_fail_closed(field,value):
    witness,receipt=inputs();receipt[field]=value
    with pytest.raises(ValueError):timing.qualify(witness,receipt)


def test_deferred_acceptance_requires_a_later_verified_feed_day():
    witness,receipt=inputs();witness['acceptance_datetime_raw']='20251030174000';reseal(witness)
    assert timing.qualify(witness,receipt)['public_time_qualified'] is True


def test_explicit_official_day_mode_requires_dates_without_fabricating_acceptance():
    witness,receipt=inputs();witness['acceptance_datetime_raw']='';reseal(witness)
    with pytest.raises(ValueError,match='acceptance'):timing.qualify(witness,receipt)
    result=timing.qualify(witness,receipt,mode='official_filing_day')
    assert result['available_at']=='2025-11-01T04:00:00+00:00' and result['exact_posting_time'] is False
    witness['filing_date_change']=None;reseal(witness)
    with pytest.raises(ValueError,match='dates'):timing.qualify(witness,receipt,mode='official_filing_day')


def test_representation_day_preserves_old_policy_and_never_backdates_change_date():
    w,r=inputs();w.update(filing_date_change='2025-10-30',acceptance_datetime_raw='');reseal(w)
    with pytest.raises(ValueError):timing.qualify(w,r,mode='official_filing_day')
    q=timing.qualify(w,r,mode='official_feed_representation_day')
    assert q['version']=='verified-sec-feed-representation-day-v2'
    assert q['available_at']=='2025-11-01T04:00:00+00:00'
    r['sha256']='b'*64
    with pytest.raises(ValueError):timing.qualify(w,r,mode='official_feed_representation_day')


@pytest.mark.parametrize('change',[None,'','2025-11-01','2025-10-31','2025-02-30'])
def test_representation_day_rejects_unknown_or_invalid_chronology(change):
    w,r=inputs();w['filing_date_change']=change;reseal(w)
    with pytest.raises(ValueError):timing.qualify(w,r,mode='official_feed_representation_day')
