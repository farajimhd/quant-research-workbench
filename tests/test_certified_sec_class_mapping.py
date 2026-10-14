from datetime import datetime,timezone
import pytest

from pipelines.certified_research import sec_class_mapping as mapping,sec_security_cover,sec_public_archive_time
from pipelines.certified_research.identity import ListingInterval
from pipelines.certified_research.contracts import digest
from tests.test_certified_sec_security_cover import payload
from tests.test_certified_sec_public_archive_time import inputs
from hashlib import sha256


def source():
    text=payload();cover=sec_security_cover.extract(text,sha256(text.encode()).hexdigest(),'320193')
    witness,receipt=inputs();witness.pop('witness_hash');witness['document_hash']=cover['source_text_hash'];witness['witness_hash']=digest(witness)
    return cover,witness,sec_public_archive_time.qualify(witness,receipt),receipt,


def run(interval=None,aliases=None):
    cover,witness,qualified,receipt=source()
    default=ListingInterval('listing','security','issuer','AAPL',1,datetime(2025,1,1,tzinfo=timezone.utc),
        datetime(2026,1,1,tzinfo=timezone.utc),'US','XNAS','USD','STK',True,True,False,False,'a'*64)
    return mapping.resolve(cover,witness,qualified,receipt,dict(ready=True,source_hash='b'*64,intervals=(interval or default,)),
        {'issuer':('320193',)},aliases or {'Nasdaq':('XNAS',)})


def test_exact_dated_match_is_only_a_candidate():
    result=run();row=result['links'][0]
    assert row['status']=='mapped_candidate' and row['security_id']=='security'
    assert result['allocation_certified'] is False and result['class_completeness_proven'] is False


def test_exchange_ambiguity_and_expired_listing_cannot_match():
    assert run(aliases={'Nasdaq':('XNAS','XNYS')})['links'][0]['reason']=='missing_or_ambiguous_exchange_alias'
    interval=ListingInterval('listing','security','issuer','AAPL',1,datetime(2025,1,1,tzinfo=timezone.utc),
        datetime(2025,10,1,tzinfo=timezone.utc),'US','XNAS','USD','STK',True,True,False,False,'a'*64)
    assert run(interval)['links'][0]['status']=='unresolved'


def test_changed_source_cover_cannot_be_mapped():
    cover,witness,qualified,receipt=source();cover['cik']='1'
    with pytest.raises(ValueError,match='fingerprint'):
        mapping.resolve(cover,witness,qualified,receipt,{}, {}, {})
