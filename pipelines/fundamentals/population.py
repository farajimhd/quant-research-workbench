"""Explicit population export and fail-closed dated identity resolution."""
from datetime import date, datetime, timezone
from pipelines.certified_research.contracts import literal, digest
from .adapters import ADAPTERS


def snapshot_population(query, day):
    if type(day) is not date:raise ValueError('Explicit universe snapshot date required')
    rows=query('SELECT symbol_id,security_id,issuer_id,ticker FROM q_live.feature_tradable_universe_v1 FINAL '
        f'WHERE universe_date=toDate({literal(day)}) AND is_tradable=1 ORDER BY symbol_id')
    if not rows:raise ValueError('Requested universe snapshot is absent')
    symbols=('SELECT symbol_id FROM q_live.feature_tradable_universe_v1 FINAL '
        f'WHERE universe_date=toDate({literal(day)}) AND is_tradable=1')
    bridge=query('SELECT symbol_id,cik,mapping_status,ambiguity_status FROM q_live.id_sec_market_bridge_v3 FINAL '
        f'WHERE symbol_id IN ({symbols}) ORDER BY symbol_id,cik')
    admitted=[x for x in bridge if x['mapping_status']=='active' and x['ambiguity_status']=='unique']
    by_symbol={}
    for x in admitted:by_symbol.setdefault(x['symbol_id'],set()).add(x['cik'])
    ciks=sorted({next(iter(v)) for v in by_symbol.values() if len(v)==1})
    if not ciks:raise ValueError('No unambiguous SEC issuer mapping for this population')
    kinds={'security':sorted({x['security_id'] for x in rows if x['security_id']}),
           'issuer':sorted({x['issuer_id'] for x in rows if x['issuer_id']}),'issuer_cik':ciks}
    return {'families':{f:kinds[a.kind] for f,a in ADAPTERS.items()},
        'captured_at':datetime.now(timezone.utc).isoformat(),
        'provenance':{'policy':'retained_snapshot_population_v1','universe_date':str(day),
            'population_rows':len(rows),'population_hash':digest(rows),'bridge_hash':digest(bridge),
            'sec_mapped_symbols':sum(len(v)==1 for v in by_symbol.values()),
            'sec_unresolved_symbols':len(rows)-sum(len(v)==1 for v in by_symbol.values()),
            'excluded_bridge_rows':len(bridge)-len(admitted),
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
    bridge=query('SELECT DISTINCT issuer_id,cik FROM q_live.id_sec_market_bridge_v3 FINAL '
        f'WHERE security_id={literal(security)} '
        f"AND mapping_status='active' AND ambiguity_status='unique' "
        f'AND isNotNull(valid_from_date) AND valid_from_date<=toDate({day}) '
        f'AND (isNull(valid_to_date_exclusive) OR valid_to_date_exclusive>toDate({day}))'
        + ('' if known_at is None else f' AND inserted_at<={_timestamp(known_at)}'))
    unique={(x['issuer_id'],x['cik']) for x in bridge}
    return {'security':security,'issuer':next(iter(unique))[0] if len(unique)==1 else None,
        'issuer_cik':next(iter(unique))[1] if len(unique)==1 else None,
        'issuer_mapping_status':'resolved' if len(unique)==1 else 'missing_or_ambiguous'}
