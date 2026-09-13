from copy import deepcopy
import pytest

from services.reference_gateway.deferred_identity_repair import classify_deferred, decide_retirements


def fixture():
    provider = dict(ticker='ABC', type='CS', active=True, market='stocks', locale='us',
                    currency_name='usd', name='Alpha Beta Corp', primary_exchange='XNYS', composite_figi='FIGI1')
    symbols = [dict(symbol_id='old', listing_id='l1', ticker='ABC', status='active', primary_symbol_flag=1),
               dict(symbol_id='new', listing_id='l2', ticker='ABC', status='active', primary_symbol_flag=1)]
    listings = [dict(listing_id='l1', security_id='s1', listing_status='active', currency_code='USD', ibkr_conid=1),
                dict(listing_id='l2', security_id='s1', listing_status='active', currency_code='USD', ibkr_conid=2)]
    identifiers = [dict(security_id='s1', identifier_value_normalized='FIGI1', identifier_kind='composite_figi')]
    definitions = [dict(conid=1, ticker='ABC.OLD', assetClass='STK', currency='USD', countryCode='US', listingExchange='NYSE', name='Alpha Beta Corp'),
                   dict(conid=2, ticker='ABC', assetClass='STK', currency='USD', countryCode='US', listingExchange='NYSE', name='Alpha Beta Corp')]
    return [provider, symbols, listings, identifiers, definitions]


def decide(data):
    return decide_retirements('ABC', *data, '2026-09-13')


def test_only_deferred_common_shares_admitted():
    inventory = [dict(ticker=t, type=k, active=True) for t, k in [('ABC','CS'),('ETF','ETF'),('ADR','ADRC'),('UNIT','UNIT'),('ABRpD','PFD')]]
    rows = [dict(ticker=t, status='deferred', reason='identity') for t in ['ABC','ETF','ADR','UNIT','ABR PRD','UNKNOWN']]
    rows.append(dict(ticker='DONE', status='complete'))
    result = {r['ticker']:r['classification'] for r in classify_deferred(rows, inventory)}
    assert result == dict(ABC='common_share_candidate', ETF='excluded_non_common', ADR='excluded_non_common', UNIT='excluded_non_common', **{'ABR PRD':'excluded_non_common','UNKNOWN':'unresolved_type'})


def test_exact_broker_and_figi_select_current_alias_without_modifying_history():
    data = fixture()
    before = deepcopy(data)
    result = decide(data)
    assert result['status'] == 'repairable'
    assert result['winner']['symbol_id'] == 'new'
    assert [s['symbol_id'] for s in result['losers']] == ['old']
    assert data == before


def test_two_valid_broker_contracts_never_choose_by_conid():
    data = fixture()
    data[4][0]['ticker'] = 'ABC'
    assert decide(data)['status'] == 'blocked'


def test_name_mismatch_blocks():
    data = fixture()
    data[4][1]['name'] = 'Unrelated Issuer'
    assert decide(data)['status'] == 'blocked'


def test_missing_loser_definition_blocks():
    data = fixture()
    data[4].pop(0)
    assert decide(data)['reason'] == 'losing_contract_definition_missing'


def test_conflicting_or_expired_figi_blocks():
    data = fixture()
    data[3].append(dict(data[3][0], security_id='s2'))
    assert decide(data)['reason'] == 'provider_figi_not_bound_to_one_current_security'
    data = fixture()
    data[3][0]['valid_to_date_exclusive'] = '2026-09-13'
    assert decide(data)['status'] == 'blocked'


def test_already_unique_and_foreign_ticker_untouched():
    data = fixture()
    data[1][0]['ticker'] = 'OTHER'
    result = decide(data)
    assert result['status'] == 'already_unique'
    assert result['losers'] == []


def test_repair_restart_and_concurrent_edit_guard():
    from scripts.repair_deferred_v7_identities import checked_pending
    change = dict(key='alias', before={'status':'active'}, after={'status':'inactive'})
    assert checked_pending([change], {'alias':change['before']}) == [change['after']]
    assert checked_pending([change], {'alias':change['after']}) == []
    with pytest.raises(ValueError, match='changed since preparation'):
        checked_pending([change], {'alias':{'status':'quarantined'}})
