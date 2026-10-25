"""Explicit population export and fail-closed dated identity resolution."""
from datetime import date, datetime, timezone
from pipelines.certified_research.contracts import literal, digest
from .adapters import ADAPTERS
from . import eligibility
from pipelines.reference_data.identity_review import METHOD as REVIEWED_DIRECT_METHOD
from pipelines.reference_data.identity_review import rejected_direct

DIRECT_METHOD = 'issuer_cik_to_active_market_listing'
ASSOCIATION_METHOD = 'filing_issuer_to_listed_parent'


def direct_rows(rows):
    """An association describes relevance, never the identity of a stock."""
    return [r for r in rows if r['mapping_method'] in (DIRECT_METHOD,REVIEWED_DIRECT_METHOD)
            and not rejected_direct(r.get('security_id'),r.get('cik'))
            and r['mapping_status']=='active'
            and r['ambiguity_status'] in ('unique','ambiguous_multi_listing')]


def snapshot_population(query, day):
    if type(day) is not date:raise ValueError('Explicit universe snapshot date required')
    rows=query('SELECT symbol_id,security_id,issuer_id,ticker FROM q_live.feature_tradable_universe_v1 FINAL '
        f'WHERE universe_date=toDate({literal(day)}) AND is_tradable=1 ORDER BY symbol_id')
    if not rows:raise ValueError('Requested universe snapshot is absent')
    symbols=('SELECT symbol_id FROM q_live.feature_tradable_universe_v1 FINAL '
        f'WHERE universe_date=toDate({literal(day)}) AND is_tradable=1')
    bridge=query('SELECT symbol_id,security_id,cik,mapping_status,ambiguity_status,mapping_method,valid_from_date,valid_to_date_exclusive FROM q_live.id_sec_market_bridge_v3 FINAL '
        f'WHERE symbol_id IN ({symbols}) ORDER BY symbol_id,cik')
    # Bridge ambiguity counts listings per CIK, not CIKs per symbol. Multiple
    # listings are valid issuer links; competing CIKs still fail closed below.
    admitted=[r for r in direct_rows(bridge) if r['mapping_method']!=REVIEWED_DIRECT_METHOD
        or (r.get('valid_from_date') and date.fromisoformat(str(r['valid_from_date']))<=day
            and (not r.get('valid_to_date_exclusive') or day<date.fromisoformat(str(r['valid_to_date_exclusive']))))]
    by_symbol={}
    for x in admitted:by_symbol.setdefault(x['symbol_id'],set()).add(x['cik'])
    identifiers=query('SELECT issuer_id,identifier_value_normalized AS cik FROM q_live.id_issuer_identifier_v1 FINAL '
        f'WHERE lower(identifier_kind)=\'cik\' AND issuer_id IN (SELECT issuer_id FROM q_live.feature_tradable_universe_v1 FINAL '
        f'WHERE universe_date=toDate({literal(day)}) AND is_tradable=1) ORDER BY issuer_id,cik')
    by_issuer={}
    for x in identifiers:
        cik=x['cik']
        if isinstance(cik,str) and len(cik)==10 and cik.isascii() and cik.isdigit() and int(cik)>0:
            by_issuer.setdefault(x['issuer_id'],set()).add(cik)
    # Snapshot issuer identifiers are not dated ticker history. Never use this
    # path to bypass a competing, rejected or ambiguous direct bridge.
    bridged={x['symbol_id'] for x in bridge if x['mapping_method']!=ASSOCIATION_METHOD}
    issuer_resolved=0
    for row in rows:
        candidates=by_issuer.get(row['issuer_id'],set())
        if (row['symbol_id'] not in bridged and len(candidates)==1
                and not rejected_direct(row['security_id'],next(iter(candidates)))):
            by_symbol[row['symbol_id']]=candidates
            issuer_resolved+=1
    excluded=[];included=[]
    for row in rows:
        candidates=by_symbol.get(row['symbol_id'],set())
        if (eligibility.blocked('security',row['security_id'])
                or any(eligibility.blocked('issuer_cik',c) for c in candidates)):
            excluded.append(dict(row,reason=eligibility.REASON,ciks=sorted(candidates)))
        else:included.append(row)
    included_symbols={r['symbol_id'] for r in included}
    by_symbol={s:v for s,v in by_symbol.items() if s in included_symbols}
    ciks=sorted({next(iter(v)) for v in by_symbol.values() if len(v)==1})
    if not ciks:raise ValueError('No unambiguous SEC issuer mapping for this population')
    kinds={'security':sorted({x['security_id'] for x in included if x['security_id']}),
           'issuer':sorted({x['issuer_id'] for x in included if x['issuer_id']}),'issuer_cik':ciks}
    return {'families':{f:kinds[a.kind] for f,a in ADAPTERS.items()},
        'captured_at':datetime.now(timezone.utc).isoformat(),
        'provenance':{'policy':'retained_snapshot_population_v2_direct_issuer','universe_date':str(day),
            'population_rows':len(rows),'population_hash':digest(rows),'bridge_hash':digest(bridge),
            'eligible_symbols':len(included),'exclusions':excluded,'eligibility_policy':eligibility.policy(),
            'issuer_identifier_hash':digest(identifiers),'sec_issuer_identifier_symbols':issuer_resolved,
            'sec_mapped_symbols':sum(len(v)==1 for v in by_symbol.values()),
            'sec_unresolved_symbols':len(included)-sum(len(v)==1 for v in by_symbol.values()),
            'excluded_bridge_rows':len(bridge)-len(admitted),
            'subsidiary_association_rows':sum(x['mapping_method']==ASSOCIATION_METHOD for x in bridge),
            'scope':'Snapshot cohort only; not historical membership or current account buy permission'}}


def resolve_ticker(query, ticker, at, *, known_at=None):
    """No fuzzy/current-ticker substitution. Unknown historical mapping blocks."""
    from .queries import _timestamp
    _timestamp(at)
    if not isinstance(ticker,str) or not ticker or len(ticker)>64:raise ValueError('Invalid ticker')
    day=literal(at.astimezone(timezone.utc).date())
    knowledge='' if known_at is None else f' AND observed_at_utc<={_timestamp(known_at)}'
    rows=query('SELECT DISTINCT security_id,listing_id FROM q_live.id_symbol_interval_v1 FINAL '
        f'WHERE ticker_normalized={literal(ticker.upper())} AND valid_from_date<=toDate({day}) '
        f'AND (isNull(valid_to_date_exclusive) OR valid_to_date_exclusive>toDate({day})) '
        f"AND mapping_status='mapped' AND is_deleted=0{knowledge}")
    if len({x['security_id'] for x in rows})!=1:
        raise ValueError('Ticker mapping absent or ambiguous at requested timestamp')
    security=rows[0]['security_id']
    eligibility.require_eligible('security',[security])
    bridge=query('SELECT DISTINCT issuer_id,cik FROM q_live.id_sec_market_bridge_v3 FINAL '
        f'WHERE security_id={literal(security)} '
        f'AND mapping_method IN ({literal(DIRECT_METHOD)},{literal(REVIEWED_DIRECT_METHOD)}) '
        f"AND mapping_status='active' AND ambiguity_status IN ('unique','ambiguous_multi_listing') "
        f'AND isNotNull(valid_from_date) AND valid_from_date<=toDate({day}) '
        f'AND (isNull(valid_to_date_exclusive) OR valid_to_date_exclusive>toDate({day}))'
        + ('' if known_at is None else f' AND inserted_at<={_timestamp(known_at)}'))
    rejected=[x for x in bridge if rejected_direct(security,x['cik'])]
    unique={(x['issuer_id'],x['cik']) for x in bridge if not rejected_direct(security,x['cik'])}
    eligibility.require_eligible('issuer_cik',[cik for _,cik in unique])
    return {'security':security,'issuer':next(iter(unique))[0] if len(unique)==1 else None,
        'issuer_cik':next(iter(unique))[1] if len(unique)==1 else None,
        'issuer_mapping_status':'resolved' if len(unique)==1 else 'rejected_direct_identity' if rejected else 'missing_or_ambiguous'}
