"""Evidence-based current-symbol retirement; never guesses from a shared ticker.

Retirement affects current symbol/source aliases only. Listings, securities and
historical ticker intervals remain intact for point-in-time consumers.
"""
from dataclasses import asdict
import re

from services.reference_gateway.ibkr_contract_identity import normalize_equity_symbol, resolve_massive_ibkr_contract


def classify_deferred(rows, inventory):
    """Classify only the frozen deferred population; admission requires CS.

Preferred suffix normalization is diagnostic only. It never admits a common
share by collapsing its class suffix into a different ticker.
    """
    by_key = {}
    for item in inventory:
        by_key.setdefault(normalize_equity_symbol(item.get('ticker')), []).append(item)
    result = []
    for row in rows:
        if row['status'] != 'deferred':
            continue
        ticker = row['ticker']
        matched = by_key.get(normalize_equity_symbol(ticker), [])
        method = 'equity_separator_normalization'
        preferred = re.fullmatch(r'(.+) PR([A-Z])', ticker)
        if not matched and preferred:
            matched = by_key.get(normalize_equity_symbol(preferred[1] + 'p' + preferred[2]), [])
            method = 'preferred_suffix_type_lookup_only'
        active = [p for p in matched if p.get('active')]
        evidence = active or matched
        types = {p.get('type') for p in evidence if p.get('type')}
        kind = next(iter(types)) if len(types) == 1 else None
        status = 'common_share_candidate' if kind == 'CS' and method != 'preferred_suffix_type_lookup_only' else 'excluded_non_common' if kind and kind != 'CS' else 'unresolved_type'
        result.append(dict(ticker=ticker,original_reason=row['reason'],classification=status,
                           provider_type=kind,match_method=method,provider_records=evidence))
    return result


def decide_retirements(ticker, provider, symbols, listings, identifiers, definitions, as_of_date):
    """Require current provider FIGI + a unique exact broker contract + name.

The selected contract must already have one canonical symbol owner. Different
listing IDs are allowed among losers only after their conids were checked.
    """
    def blocked(reason, **extra):
        return dict(ticker=ticker,status='blocked',reason=reason,**extra)
    if provider.get('type') != 'CS' or not provider.get('active'):
        return blocked('provider_not_active_common_share')
    if normalize_equity_symbol(provider.get('ticker')) != normalize_equity_symbol(ticker):
        return blocked('provider_symbol_or_share_class_mismatch')
    if provider.get('market') != 'stocks' or provider.get('locale') != 'us' or str(provider.get('currency_name','')).upper() != 'USD':
        return blocked('provider_market_locale_or_currency_mismatch')
    resolution = resolve_massive_ibkr_contract(massive_ticker=provider['ticker'],massive_name=provider.get('name',''),
        massive_exchange=provider.get('primary_exchange',''),definitions=definitions)
    if not resolution.accepted or not resolution.company_name_match:
        return blocked('broker_identity_not_unique_and_name_verified',broker=asdict(resolution))
    figis = {provider.get('composite_figi'),provider.get('share_class_figi')} - {None,''}
    security_ids = {i['security_id'] for i in identifiers if i.get('identifier_value_normalized') in figis
        and i.get('identifier_kind','').lower() in ('figi','composite_figi','share_class_figi')
        and (not i.get('valid_from_date') or i['valid_from_date'] <= as_of_date)
        and (not i.get('valid_to_date_exclusive') or as_of_date < i['valid_to_date_exclusive'])}
    if len(security_ids) != 1:
        return blocked('provider_figi_not_bound_to_one_current_security')
    security = next(iter(security_ids))
    active = [s for s in symbols if s['status'] == 'active' and s['primary_symbol_flag']
              and normalize_equity_symbol(s['ticker']) == normalize_equity_symbol(ticker)]
    by_listing = {l['listing_id']:l for l in listings}
    scoped = [s for s in active if s['listing_id'] in by_listing and by_listing[s['listing_id']]['listing_status']=='active'
              and by_listing[s['listing_id']]['currency_code']=='USD']
    winners = [s for s in scoped if by_listing[s['listing_id']]['security_id']==security
               and str(by_listing[s['listing_id']].get('ibkr_conid')) == str(resolution.conid)]
    # Same-listing aliases use the existing gateway preference for a single
    # non-Massive primary owner. Different listings must not be guessed between.
    if len(winners)>1 and len({s['listing_id'] for s in winners})==1:
        primary = [s for s in winners if s.get('source_system','').lower() != 'massive']
        if len(primary)==1:
            winners=primary
    if len(winners) != 1:
        return blocked('verified_contract_has_no_unique_canonical_symbol_owner',broker=asdict(resolution))
    winner = winners[0]
    losers = [s for s in scoped if s['symbol_id']!=winner['symbol_id']]
    known = {str(d.get('conid') or d.get('con_id')) for d in definitions}
    if any(str(by_listing[s['listing_id']].get('ibkr_conid')) not in known for s in losers):
        return blocked('losing_contract_definition_missing')
    return dict(ticker=ticker,status='repairable' if losers else 'already_unique',provider_ticker=provider['ticker'],
                winner=winner,losers=losers,security_id=security,broker=asdict(resolution))
