from datetime import date

import pytest

from pipelines.certified_research import universe


def row(**changes):
    return dict(dict(listing_id='listing:1',security_id='security:1',issuer_id='issuer:1',conids=['91'],
        exchange_code='XNYS',currency_code='USD',listing_status='active',list_date='2024-01-01',delisted_date=None,
        product_type='STK',security_status='active',exchange_country='US',symbol_interval_id='interval:1',ticker='TEST',
        valid_from_date='2024-01-01',valid_to_date_exclusive=None,interval_mapping='mapped',history_status='completed',
        history_mapping='mapped',interval_security_id='security:1',history_security_id='security:1',history_listing_id='listing:1',
        durable_issuer=1,duplicate_issuer=0,mapping_issue=0,source_hashes_valid=1,evidence_hash='a'*64,
        history_last_success_at_utc='2026-10-03 04:00:00'),**changes)


@pytest.mark.parametrize('timestamp,reason',[(None,'unknown_ticker_history_cutoff'),('invalid','unknown_ticker_history_cutoff'),
    ('2026-10-02 06:00:00','stale_ticker_history')])
def test_success_status_does_not_certify_dates_after_its_observation(timestamp,reason):
    result=universe.resolve([row(history_last_success_at_utc=timestamp)],date(2026,1,1),date(2026,10,2))
    assert result['rejection_counts']=={reason:1} and not result['ready']


@pytest.mark.parametrize('end,cutoff',[(date(2026,1,2),'2026-01-03 05:00:00'),(date(2026,10,2),'2026-10-03 04:00:00')])
def test_cutoff_uses_new_york_date_boundary_and_dst(end,cutoff):
    assert universe.resolve([row(history_last_success_at_utc=cutoff)],date(2026,1,1),end)['ready']


def test_closed_symbol_and_listing_need_only_their_own_end_boundary():
    data=row(listing_status='inactive',security_status='inactive',delisted_date='2026-05-01',
        valid_to_date_exclusive='2026-03-01',history_last_success_at_utc='2026-03-01 05:00:00')
    result=universe.resolve([data],date(2026,1,1),date(2026,10,2))
    assert result['ready'] and result['intervals'][0].valid_to.date()==date(2026,3,1)


def test_source_fingerprint_contains_the_coverage_cutoff():
    sql=universe.candidates_sql(date(2026,1,1),date(2026,10,2),date(2026,10,14))
    assert 'c.last_success_at_utc history_last_success_at_utc' in sql
    assert 'c.source_status,c.mapping_status,if(c.last_success_at_utc IS NULL,NULL,least(c.last_success_at_utc,' in sql
    assert "'America/New_York'),'UTC')" in sql
