"""Explicit research assumption; never alters Reference Gateway live listings."""
from collections import Counter,defaultdict
from datetime import date,datetime,time,timedelta
import hashlib,json
from zoneinfo import ZoneInfo
from . import batch_conid_pool,universe
from .contracts import digest,literal
from .identity import ListingInterval,validate_intervals
from services.reference_gateway.tradability import is_otc_venue,SUPPORTED_PRODUCT_TYPES

POLICY='current_conid_retrospective_v1'


def compile_snapshot(base,pool,metadata,start,end,anchor):
    """Back-project reviewed identities while honoring known lifecycle/conflicts.

    Unknown historical identity is assumed; contrary retained identity is not.
    A quarantined candidate remains in the census and cannot become a member.
    """
    by_listing={r['listing_id']:r for r in metadata}
    history=defaultdict(list)
    for row in base['source_rows']:history[row['listing_id']].append(row)
    ny=ZoneInfo('America/New_York');intervals=[];proofs={};quarantine=[];census=Counter();outside=0
    for candidate in pool['rows']:
        kind=candidate['provider_type'] or '<missing>'
        if not candidate['event_backed']:
            census[(kind,'no_target_events')]+=1;continue
        reason=''
        listings=[by_listing[i] for i in candidate['listing_ids'] if i in by_listing]
        if candidate['status']!='resolved' or candidate['conid']<=0:reason='unresolved_conid'
        elif len(listings)!=1:reason='ambiguous_current_listing'
        else:
            current=listings[0]
            if current['security_id']!=candidate['security_id'] or not current['issuer_id']:
                reason='conflicting_current_identity'
            elif current.get('currency_code')!='USD' or current.get('product_type','').upper() not in SUPPORTED_PRODUCT_TYPES:
                reason='unsupported_security'
            elif current.get('exchange_country','').upper()!='US':reason='unproven_us_security'
            elif not current.get('exchange_code'):reason='unproven_exchange'
            elif is_otc_venue(*(current.get(k) for k in ('exchange_code','acronym','mic','operating_mic','exchange_name'))):
                reason='unsupported_otc_venue'
        if reason:
            census[(kind,'quarantined')]+=1
            quarantine.append(dict(entity_key=candidate['entity_key'],ticker=candidate['ticker'],reason=reason));continue
        listing=current['listing_id'];known=history[listing]
        conflict=any(r.get('interval_security_id') and r['interval_security_id']!=candidate['security_id'] for r in known)
        if conflict:
            census[(kind,'quarantined')]+=1
            quarantine.append(dict(entity_key=candidate['entity_key'],ticker=candidate['ticker'],reason='conflicting_historical_identity'));continue
        # Keep known rename intervals; infer the current symbol only if there
        # are no retained symbols. Never overwrite contrary symbol evidence.
        symbols=[r for r in known if r.get('ticker') and r.get('valid_from_date')]
        if not symbols:symbols=[dict(ticker=candidate['ticker'],valid_from_date=None,valid_to_date_exclusive=None)]
        symbols=list({(r['ticker'],str(r.get('valid_from_date')),str(r.get('valid_to_date_exclusive'))):r for r in symbols}.values())
        symbols.sort(key=lambda r:str(r.get('valid_from_date') or start))
        before=len(intervals)
        for index,symbol in enumerate(symbols):
            # Earliest retained observation is not proof of identity birth.
            # The approved retrospective assumption fills earlier unknown
            # identity, while known IPO/delisting and rename boundaries remain.
            symbol_start=symbol.get('valid_from_date') if index else None
            first=max([start]+[date.fromisoformat(str(v)) for v in (current.get('list_date'),symbol_start) if v])
            last=min([end+timedelta(days=1)]+[date.fromisoformat(str(v)) for v in (current.get('delisted_date'),symbol.get('valid_to_date_exclusive')) if v])
            if first>=last:continue
            proof=json.dumps(dict(policy=POLICY,pool_hash=pool['pool_hash'],candidate=candidate,
                current=current,symbol=symbol,start=str(first),end_exclusive=str(last)),sort_keys=True,default=str,separators=(',',':'))
            fingerprint=hashlib.sha256(proof.encode()).hexdigest();proofs[fingerprint]=proof
            # This interval describes research identity, not dated exchange or
            # broker availability. Event venue filtering remains a separate concern.
            intervals.append(ListingInterval(listing,candidate['security_id'],current['issuer_id'],symbol['ticker'],
                int(candidate['conid']),datetime.combine(first,time(),ny),datetime.combine(last,time(),ny),
                'US',current['exchange_code'],current['currency_code'],current['product_type'],True,True,False,False,fingerprint))
        if len(intervals)>before:census[(kind,'admitted')]+=1
        else:census[(kind,'outside_context')]+=1;outside+=1
    intervals=validate_intervals(tuple(intervals))
    result=dict(intervals=intervals,rejected=(),rejection_counts={},ready=bool(intervals),source_rows=(),
        context_start=start,end=end,anchor_date=anchor,identity_policy=POLICY,assumption_proofs=proofs,
        historical_membership_certified=False,quarantine=tuple(quarantine),
        quarantine_counts=dict(Counter(r['reason'] for r in quarantine)),outside_context=outside,outside_scope={},
        eligibility_census={kind:{outcome:n for (k,outcome),n in sorted(census.items()) if k==kind}
            for kind in sorted({k for k,_ in census})},
        conid_pool=dict(pool_hash=pool['pool_hash'],review_hash=pool['review_hash'],summary=pool['summary']))
    result['source_hash']=digest(dict(policy=POLICY,base=base['source_hash'],pool=pool['pool_hash'],
        metadata=metadata,proofs=sorted(proofs),quarantine=quarantine,start=start,end=end,anchor=anchor))
    return result


def load(query,start,end,anchor,review_hash):
    if not review_hash:raise ValueError('Retrospective identity requires a pinned reviewed pool')
    base=universe.load(query,start,end,anchor)
    pool=batch_conid_pool.load(query,review_hash,max(start,date(2026,1,1)),end)
    ids=sorted({i for r in pool['rows'] if r['status']=='resolved' and r['event_backed'] for i in r['listing_ids']})
    metadata=[]
    for offset in range(0,len(ids),256):
        keys=','.join(map(literal,ids[offset:offset+256]))
        metadata.extend(query('SELECT l.listing_id AS listing_id,l.security_id AS security_id,s.issuer_id AS issuer_id,'
            'l.exchange_code AS exchange_code,l.currency_code AS currency_code,'
            'l.list_date AS list_date,l.delisted_date AS delisted_date,s.product_type AS product_type,e.iso_country_code AS exchange_country,'
            'e.acronym AS acronym,e.mic AS mic,e.operating_mic AS operating_mic,e.name AS exchange_name '
            'FROM q_live.id_listing_v1 l FINAL LEFT JOIN q_live.id_security_v1 s FINAL ON l.security_id=s.security_id '
            'LEFT JOIN q_live.ref_exchange_v1 e FINAL ON l.exchange_code=e.exchange_code '
            f'WHERE l.listing_id IN ({keys}) ORDER BY l.listing_id'))
    if len({r['listing_id'] for r in metadata})!=len(metadata):raise ValueError('Duplicate current listing metadata')
    return compile_snapshot(base,pool,metadata,start,end,anchor)
