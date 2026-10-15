"""Publish market aggregates without inventing unavailable reference features."""
from datetime import datetime,time
from bisect import bisect_left
from zoneinfo import ZoneInfo
from . import universe,source,runtime_plan
from .campaign import PlannedUnit
from .execution import Unit,validate
from .contracts import digest

UNKNOWN_FAMILIES=('fundamentals','float','short_volume','short_interest')


def assemble(config,snapshot,canonical,reporting,definition,rules,anchor,*,query=None,guard=lambda:None):
    from .activity import windows_contract
    windows_contract(config.volume_windows_seconds)
    membership=universe.execution_membership(snapshot,canonical['expected'])
    if not membership['ready']:raise ValueError('Independent market identity membership is not ready')
    sessions=membership['sessions']
    if query is not None:sessions=source.resolve_spellings(query,sessions,guard=guard)['sessions']
    roots,parents=source.certified_units(canonical,reporting)
    args=dict(context_start=str(snapshot['context_start']),end=str(config.end),anchor_date=str(anchor),source_hash=snapshot['source_hash'])
    if config.issuer_keys:args['issuer_keys']=config.issuer_keys
    if config.conid_review_hash:args['conid_review_hash']=config.conid_review_hash
    if config.identity_policy!='dated':args['identity_policy']=config.identity_policy
    if snapshot.get('quarantine_counts'):args['identity_quarantine_counts']=snapshot['quarantine_counts']
    plans=[PlannedUnit(Unit('universe','universe'),args),*runtime_plan.units(config,definition,rules),*roots]
    owners={l:k for k,ls in membership['security_listings'].items() for l in ls}
    previous={}
    for session in sessions:
        # Context-only sessions are required for rolling activity; no SEC-based
        # price context or fabricated indicator/V7 genesis is introduced.
        prefix=f'market:{session.listing_key}:{session.day}'
        values=dict(day=str(session.day),listing_key=session.listing_key,ticker=session.ticker)
        if session.source_ticker is not None:values['source_ticker']=session.source_ticker
        aggregate=prefix+':aggregate'
        plans.append(PlannedUnit(Unit(aggregate,'market_aggregate',(*parents[session.day],'universe','calculation_recipe'),config.query_memory_bytes),values))
        stamp=int(datetime.combine(session.day,time(4),ZoneInfo('America/New_York')).timestamp())
        starts,keys=previous.setdefault(session.listing_key,([],[]))
        starts.append(stamp);keys.append(aggregate)
        first=max(0,bisect_left(starts,stamp-max(config.volume_windows_seconds))-1)
        if len(keys)-first>1024:raise ValueError('Rolling activity context exceeds its bounded parent contract')
        plans.append(PlannedUnit(Unit(prefix+':activity','activity',tuple(keys[first:]),config.query_memory_bytes),
            dict(values,windows=config.volume_windows_seconds)))
    for security in sorted({owners[s.listing_key] for s in membership['sessions']}):
        plans.append(PlannedUnit(Unit(f'reference-availability:{security}','reference_availability',('universe',)),
            dict(entity_key=security,start=str(config.start),end=str(config.end),families=UNKNOWN_FAMILIES,
                status='unknown',reason='not_qualified_in_independent_market_scope')))
    validate(tuple(p.unit for p in plans),config.global_memory_bytes)
    return dict(ready=True,plans=tuple(plans),source_hash=digest(dict(source=canonical['content_hash'],reporting=reporting['content_hash'])),
        recipe_hash=runtime_plan.resolve(config,plans)['recipe_hash'],preparation_hash=digest(dict(membership=membership['membership_hash'],scope=config.product_scope)),
        publication_scope='market_bars',deferred_products=('technical','v7'),reference_features='unknown')
