from copy import deepcopy
from datetime import date
from types import SimpleNamespace
import json

import pytest

from pipelines.certified_research import batch_conid_pool as pool, conid_case_review as review
from pipelines.certified_research.contracts import CampaignConfig, digest


def fixture():
    evidence=dict(original=dict(ticker='BCPC'))
    decision=dict(entity_key='figi:one',ticker='BCPC',provider_active=1,original_reason='open_issue',
        decision='identity_resolved',method='retained_contract',reason='',security_id='s1',conid=123,
        listing_ids=['l1'],candidate_conids=[123],historical_aliases=[],remaining_issues=[],evidence_hash=digest(evidence))
    decision['record_hash']=digest(decision)
    fingerprint=digest(dict(version=review.VERSION,baseline='b'*64,evidence_hash='c'*64,decisions=[decision]))
    row=dict(review_hash=fingerprint,**decision)
    header=dict(review_hash=fingerprint,version=review.VERSION,baseline_hash='b'*64,source_evidence_hash='c'*64,
        range_start='2026-01-01',range_end='2026-10-02',reviewed=1,identity_resolved=1,blocked=0,rows_hash=digest([row]))
    header['record_hash']=digest(header)
    sealed=dict(header=header,rows=[row])
    native=dict(entity_key='figi:one',ticker='BCPC',provider_type='CS',provider_active=1,
        provider_exchange='OTC',status='blocked',reason='open_issue',security_id='s1',conid=0,
        listing_ids=['l1'],evidence_hash='e'*64)
    current=dict(snapshot_hash='f'*64,rows=[native])
    proof=dict(evidence_hash=digest(evidence),payload=json.dumps(evidence))
    return current,sealed,proof


def reader(monkeypatch,sealed,proof):
    monkeypatch.setattr(review,'verify',lambda *a,**k:None)
    def query(sql):
        if review.receipt_table().name in sql:return deepcopy([sealed['header']])
        if review.evidence_table().name in sql:return deepcopy([proof])
        if review.table().name in sql:return deepcopy(sealed['rows'])
        raise AssertionError(sql)
    return query


def test_sealed_receipt_and_all_original_proofs_are_required(monkeypatch):
    _,sealed,proof=fixture();query=reader(monkeypatch,sealed,proof)
    assert pool.read_review(query,sealed['header']['review_hash'],date(2026,1,1),date(2026,10,2))==sealed
    proof['payload']='{}'
    with pytest.raises(ValueError,match='original evidence'):pool.read_review(query,sealed['header']['review_hash'],date(2026,1,1),date(2026,10,2))


def test_future_range_requires_new_review(monkeypatch):
    _,sealed,proof=fixture()
    with pytest.raises(ValueError,match='version/range'):
        pool.read_review(reader(monkeypatch,sealed,proof),sealed['header']['review_hash'],date(2026,1,1),date(2026,10,3))


def test_competing_rows_cannot_hide_behind_final(monkeypatch):
    _,sealed,proof=fixture();sealed['rows'].append(dict(sealed['rows'][0],conid=456))
    with pytest.raises(ValueError,match='census/hash'):
        pool.read_review(reader(monkeypatch,sealed,proof),sealed['header']['review_hash'],date(2026,1,1),date(2026,10,2))


def test_receipt_corruption_is_rejected(monkeypatch):
    _,sealed,proof=fixture();sealed['header']['rows_hash']='d'*64
    with pytest.raises(ValueError,match='receipt hash'):
        pool.read_review(reader(monkeypatch,sealed,proof),sealed['header']['review_hash'],date(2026,1,1),date(2026,10,2))


def test_raw_case_otc_and_inactive_are_retained():
    current,sealed,_=fixture();current['rows'][0]['provider_active']=0
    prepared=pool.compile_pool(current,sealed,{'BCPC'})
    assert prepared['rows'][0]['provider_exchange']=='OTC'
    assert prepared['summary']['inactive_retained']==1
    assert prepared['rows'][0]['ticker']=='BCPC'
    assert prepared['rows'][0]['conid']==123
    assert not prepared['historical_membership_certified']


def test_different_wire_case_does_not_match_events():
    current,sealed,_=fixture()
    assert not pool.compile_pool(current,sealed,{'BCpC'})['rows'][0]['event_backed']


def test_review_cannot_replace_competing_current_conid():
    current,sealed,_=fixture();current['rows'][0].update(status='resolved',conid=456)
    with pytest.raises(ValueError,match='conflicts'):pool.compile_pool(current,sealed,{'BCPC'})


def test_supported_contract_does_not_invent_dated_membership():
    current,sealed,_=fixture();prepared=pool.compile_pool(current,sealed,{'BCPC'})
    snapshot=dict(intervals=(),rejected=(),source_hash='1'*64,ready=True)
    result=pool.attach(snapshot,prepared)
    assert not result['ready']
    assert result['rejection_counts']=={'batch_candidate_requires_dated_membership':1}
    assert result['source_hash']!=snapshot['source_hash']


def test_case_sensitive_proven_interval_and_remaining_dependencies():
    current,sealed,_=fixture();snapshot=dict(intervals=(SimpleNamespace(listing_id='l1',security_id='s1',conid=123,ticker='BCPC'),),
        rejected=(),source_hash='1'*64,ready=True)
    assert pool.attach(snapshot,pool.compile_pool(current,sealed,{'BCPC'}))['ready']
    sealed['rows'][0]['remaining_issues']=['canonical_issuer_binding_pending']
    assert not pool.attach(snapshot,pool.compile_pool(current,sealed,{'BCPC'}))['ready']


def test_review_pin_changes_plan_but_preserves_algorithm_compatibility():
    unpinned=CampaignConfig(date(2026,1,1),date(2026,10,2))
    pinned=CampaignConfig(date(2026,1,1),date(2026,10,2),conid_review_hash='a'*64)
    assert 'conid_review_hash' not in unpinned.identity()['config']
    assert unpinned.content_hash!=pinned.content_hash
    assert unpinned.calculation_hash==pinned.calculation_hash
    with pytest.raises(ValueError,match='sealed SHA256'):CampaignConfig(date(2026,1,1),date(2026,10,2),conid_review_hash='latest')


def test_one_proven_interval_does_not_certify_later_events():
    from datetime import datetime,timezone
    current,sealed,_=fixture();prepared=pool.compile_pool(current,sealed,{'BCPC'})
    prepared.update(target_start='2026-01-01',target_end='2026-10-02')
    prepared['rows'][0].update(first_event_date='2026-01-02',last_event_date='2026-10-02')
    interval=SimpleNamespace(listing_id='l1',security_id='s1',conid=123,ticker='BCPC',
        valid_from=datetime(2026,1,1,tzinfo=timezone.utc),valid_to=datetime(2026,2,1,tzinfo=timezone.utc))
    snapshot=dict(intervals=(interval,),rejected=(),source_hash='1'*64,ready=True)
    assert pool.attach(snapshot,prepared)['rejection_counts']=={'batch_candidate_has_dated_coverage_gap':1}


def test_blocked_review_does_not_vanish_when_native_reconciliation_is_permissive():
    current,sealed,_=fixture();current['rows'][0].update(status='resolved',conid=123,reason='')
    sealed['rows'][0].update(decision='blocked',reason='contradictory_issuer',conid=0)
    result=pool.compile_pool(current,sealed,{'BCPC'})
    assert result['rows'][0]['status']=='blocked' and result['rows'][0]['conid']==0


def test_event_symbols_missing_from_provider_inventory_are_not_silently_dropped():
    current,sealed,_=fixture();prepared=pool.compile_pool(current,sealed,{'OLD_ALIAS'})
    assert prepared['unrepresented_event_tickers']==['OLD_ALIAS']
    snapshot=dict(intervals=(),rejected=(),source_hash='1'*64,ready=True)
    assert pool.attach(snapshot,prepared)['rejection_counts']=={'batch_event_symbol_requires_reference_identity':1}
