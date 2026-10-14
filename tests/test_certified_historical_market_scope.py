from datetime import date
from pipelines.certified_research import universe


def test_previously_tradable_otc_move_is_a_dependency_not_silent_exclusion():
    row=dict(listing_id='listing',ticker='TEST',conids=['123'],exchange_code='OTC',exchange_name='OTC Markets',
        exchange_country='US',currency_code='USD',product_type='STK',historically_tradable=1,evidence_hash='a'*64)
    result=universe.resolve([row],date(2026,1,1),date(2026,10,2))
    assert not result['ready']
    assert result['rejection_counts']==dict(historical_market_scope_transition_requires_evidence=1)
    assert not result['outside_scope']
    row['historically_tradable']=0
    excluded=universe.resolve([row],date(2026,1,1),date(2026,10,2))
    assert excluded['outside_scope']==dict(otc_venue=1) and not excluded['rejected']


def test_historical_non_us_move_does_not_erase_prior_eligibility():
    row=dict(listing_id='listing',ticker='TEST',conids=['123'],exchange_code='XTSE',exchange_country='CA',
        currency_code='CAD',product_type='STK',historically_tradable=1,evidence_hash='a'*64)
    result=universe.resolve([row],date(2026,1,1),date(2026,10,2))
    assert result['rejection_counts']==dict(historical_market_scope_transition_requires_evidence=1)
