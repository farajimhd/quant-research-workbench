import pytest
from pipelines.certified_research import sec_trust_context as trust
from pipelines.fundamentals.trusts import observations
from datetime import datetime, timezone


def test_exact_subject_not_parent_or_neighbor():
    axis='{http://fasb.org/srt/2021-01-31}ConsolidatedEntitiesAxis'
    def row(member):return dict(dimensions=((axis,'{http://www.edison.com/20211231}'+member),))
    assert trust.subject(row('SCETrustVIMember')) == '0001709317'
    assert trust.subject(row('SCETrustVMember')) is None
    assert trust.subject(row('SouthernCaliforniaEdisonCompanyMember')) is None
    assert trust.subject(dict(dimensions=((axis,'{urn:other}SCETrustVIMember'),))) is None
    old='{http://www.edison.com/20191231}VariableInterestEntitiesByNameOfEntityAxis'
    assert trust.subject(dict(dimensions=((old,'{http://www.edison.com/20191231}SouthernCaliforniaEdisonTrustIiMember'),))) == '0001122392'


def test_competing_subject_rejected():
    axis='{http://fasb.org/srt/2023}ConsolidatedEntitiesAxis'
    with pytest.raises(ValueError,match='Competing'):
        trust.subject(dict(dimensions=[(axis,'{http://www.edison.com/20231231}'+m) for m in ('SCETrustVIMember','SceTrustViiMember')]))


def test_source_binding():
    h=next(iter(trust.DOCUMENTS))
    assert trust.registered(h,'0000827052')
    assert not trust.registered(h,'0001709317')
    assert not trust.registered('0'*64,'0000827052')


def test_query_requires_public_and_known_time_and_receipts():
    sql=[]
    at=datetime(2026,1,1,tzinfo=timezone.utc)
    assert observations(lambda q:sql.append(q) or [],['0001709317'],at,known_at=at)==[]
    assert 'available_at<=' in sql[0] and 'observed_at<=' in sql[0]
    assert 'reference_batch_receipt_v1' in sql[0]
    assert 'SUM(' not in sql[0].upper()
    with pytest.raises(ValueError,match='Unreviewed'):
        observations(lambda _:[],['0000092103'],at)
