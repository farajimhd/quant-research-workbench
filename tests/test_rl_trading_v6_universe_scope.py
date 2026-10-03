import pytest
from research.rl_trading.v6.universe_scope import exclusion,filter_population,require_scope


def row(**changes):
    return dict(listing_id='good',exchange_code='NASDAQ',country='US',currency='USD',
                product_type='STK',mic='XNAS',**changes)


@pytest.mark.parametrize('venue',['OTCLNKECN','ARCAEDGE','PINK','OTCQX'])
def test_otc_excluded_even_if_snapshot_says_tradable(venue):
    r=row();r['exchange_code']=venue
    assert exclusion(r)=='unsupported_otc_venue'


def test_scope_fails_closed_and_receipt_binds_selected_population():
    good=row(); bad=dict(good,listing_id='bad',country='CA')
    population=[dict(listing_id=i,ticker=i) for i in ('good','bad','missing')]
    selected,proof=filter_population(population,[good,bad])
    assert [r['listing_id'] for r in selected]==['good']
    assert len(proof['excluded'])==2
    require_scope(dict(universe_scope=proof,census={'good':10}))
    with pytest.raises(ValueError,match='membership'):
        require_scope(dict(universe_scope=proof,census={'good':10,'bad':10}))
