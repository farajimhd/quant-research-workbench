"""Execution dependencies for certified market sessions, without nested pools."""
from __future__ import annotations

from dataclasses import dataclass,replace
from datetime import date,datetime,time,timedelta,timezone
from bisect import bisect_left
from zoneinfo import ZoneInfo

from .campaign import PlannedUnit,unit_id
from .contracts import CampaignConfig
from .execution import Unit


def reference_coverage_end(session_end):
    """Last UTC date touched by the exclusive 20:00 New York AH boundary."""
    if type(session_end) is not date:raise ValueError('Reference coverage requires a session date')
    boundary=datetime.combine(session_end,time(20),ZoneInfo('America/New_York')).astimezone(timezone.utc)
    return (boundary-timedelta(microseconds=1)).date()


def price_context_days(target_days,issuer_contexts):
    """Union certified float-price demands before dated universe preparation.

    Returned days are discovery demands, not proof of listing or source
    coverage. Universe preparation must resolve eligibility for each day.
    """
    from .contracts import digest
    target_days=tuple(target_days)
    if not target_days or any(type(d) is not date for d in target_days):
        raise ValueError('Price context requires nonempty target dates')
    needed=set(target_days)
    for issuer,context in issuer_contexts.items():
        if type(issuer) is not int or not 0<issuer<2**64:
            raise ValueError('Invalid float context issuer')
        expected={'measurements','sessions','fundamentals_hash','price_gap_days','sec_age_days','context_hash'}
        source_expected=(expected-{'fundamentals_hash'})|{'sec_source_hash'}
        if set(context) not in (expected,source_expected) or context['context_hash']!=digest({k:v for k,v in context.items() if k!='context_hash'}):
            raise ValueError('Float price context fingerprint differs')
        if context['price_gap_days']!=7 or context['sec_age_days']!=520:
            raise ValueError('Float price context policy differs')
        sessions=context['sessions'];measurements=context['measurements']
        if (not isinstance(sessions,tuple) or not isinstance(measurements,tuple)
                or any(type(d) is not date for d in (*sessions,*measurements))
                or tuple(sorted(set(sessions)))!=sessions or tuple(sorted(set(measurements)))!=measurements
                or any(d>max(target_days) or d<date(2019,1,2) for d in sessions)):
            raise ValueError('Float price context dates are invalid')
        needed.update(sessions)
    return tuple(sorted(needed))


def assign_float_price_sessions(membership,issuer_contexts):
    """Assign price demands through dated security/listing ownership.

    This consumes membership expanded over all requested context dates. Missing
    listed sessions are retained as explicit unavailable demands, never assigned
    to a current symbol. Callers must handle context-only historical listings
    without inventing a new indicator/V7 genesis.
    """
    from .contracts import digest
    if not membership.get('ready') or not membership.get('days'):
        raise ValueError('Float-price assignment requires admitted expanded dated membership')
    issuers=membership['security_issuers']
    if set(issuer_contexts)-set(issuers.values()):
        raise ValueError('Float-price context has an unrelated issuer')
    requested=price_context_days(membership['days'],issuer_contexts)
    if set(requested)-set(membership['days']):
        raise ValueError('Dated membership does not cover all float-price context dates')
    owners={listing:security for security,listings in membership['security_listings'].items() for listing in listings}
    context_days={k:set(v['sessions']) for k,v in issuer_contexts.items()}
    demands={};covered=set()
    for session in membership['sessions']:
        security=owners[session.listing_key];issuer=issuers[security]
        if session.day in context_days.get(issuer,()):
            demands.setdefault(session.listing_key,set()).add(session.day)
            covered.add((security,session.day))
    unavailable=tuple(dict(security_key=security,issuer_key=issuer,day=day,reason='no_admitted_listing_session')
        for security,issuer in sorted(issuers.items()) for day in issuer_contexts.get(issuer,{}).get('sessions',())
        if (security,day) not in covered)
    result=dict(price_sessions={k:tuple(sorted(v)) for k,v in sorted(demands.items())},unavailable=unavailable,
        membership_hash=membership['membership_hash'],context_hashes={k:v['context_hash'] for k,v in sorted(issuer_contexts.items())})
    result['assignment_hash']=digest(result)
    return result


@dataclass(frozen=True)
class MarketSession:
    day: date
    listing_key: int
    ticker: str
    source_ticker: str|None=None

    def __post_init__(self):
        if (type(self.day) is not date or type(self.listing_key) is not int
                or not 0<self.listing_key<2**64 or not isinstance(self.ticker,str)
                or not self.ticker or len(self.ticker.encode())>64):
            raise ValueError('Invalid dated market session')
        if self.source_ticker is not None and (not isinstance(self.source_ticker,str) or not self.source_ticker
                or self.source_ticker.upper()!=self.ticker.upper()):raise ValueError('Invalid dated source spelling')


def market_units(config: CampaignConfig,sessions,*,source_parents,universe_parent,seed_parents,corporate_parents,state_start_dates=None,price_only_listings=()):
    """Compile source/repair→aggregate→activity,indicators,V7 dependencies.

    Callers supply only admitted dated sessions and already planned source,
    universe and certified-context units. A source parent means acquisition
    AND reporting repair have verified; it must not be merely a download.
    First-session seed parents may certify an explicit genesis, but are never
    omitted. This function does not infer eligibility or manufacture context.
    """
    sessions=tuple(sorted(sessions,key=lambda s:(s.listing_key,s.day)))
    if not sessions:raise ValueError('No admitted market sessions')
    price_only_listings=set(price_only_listings)
    if (price_only_listings-{s.listing_key for s in sessions}
            or any(s.day>=config.start for s in sessions if s.listing_key in price_only_listings)):
        raise ValueError('Price-only listings must have exclusively earlier context sessions')
    state_start_dates={} if state_start_dates is None else dict(state_start_dates)
    if set(state_start_dates)-{s.listing_key for s in sessions}:raise ValueError('State start belongs to an unplanned listing')
    target_first={}
    for session in sessions:
        if session.day>=config.start:target_first.setdefault(session.listing_key,session.day)
    for listing,starts in state_start_dates.items():
        if not isinstance(starts,tuple) or len(starts)!=2 or any(type(d) is not date or d>target_first.get(listing,config.end) for d in starts):
            raise ValueError('Indicator/V7 state starts must include the target range')
    from .activity import windows_contract
    windows_contract(config.volume_windows_seconds)
    unit_id(universe_parent);seen=set();prior={};result=[];liquidity={}
    for session in sessions:
        pair=(session.listing_key,session.day)
        if pair in seen:raise ValueError('Duplicate listing/session; resolve symbol membership first')
        if not date(2019,1,2)<=session.day<=config.end:raise ValueError('Session outside canonical context/target bounds')
        seen.add(pair)
        sources=tuple(source_parents.get(session.day,()))
        if not sources or len(set(sources))!=len(sources):raise ValueError('Certified source/repair prerequisites are missing or duplicated')
        for key in sources:unit_id(key)
        if session.listing_key in price_only_listings:
            args=dict(day=str(session.day),listing_key=session.listing_key,ticker=session.ticker)
            if session.source_ticker is not None:args['source_ticker']=session.source_ticker
            result.append(PlannedUnit(Unit(f'market:{session.listing_key}:{session.day}:aggregate','market_aggregate',
                tuple(dict.fromkeys((*sources,universe_parent))),config.query_memory_bytes),args))
            continue
        predecessors=prior.get(session.listing_key,seed_parents.get(session.listing_key))
        if not isinstance(predecessors,tuple) or len(predecessors)!=2 or not all(predecessors):
            raise ValueError('Certified indicator/V7 seed prerequisites are missing')
        for key in predecessors:unit_id(key)
        corporate=corporate_parents.get(session.listing_key)
        if not corporate:raise ValueError('Certified corporate adjustment prerequisite is missing')
        unit_id(corporate)
        prefix=f'market:{session.listing_key}:{session.day}'
        aggregate=prefix+':aggregate';technical=prefix+':technical';v7=prefix+':v7'
        args=dict(day=str(session.day),listing_key=session.listing_key,ticker=session.ticker)
        if session.source_ticker is not None:args['source_ticker']=session.source_ticker
        state_starts=state_start_dates.get(session.listing_key,(session.day,session.day))
        dependencies=tuple(dict.fromkeys((*sources,universe_parent)))
        for key,kind,parents in ((aggregate,'market_aggregate',dependencies),
                (technical,'technical',(aggregate,predecessors[0],corporate)),(v7,'v7',(aggregate,predecessors[1],corporate))):
            if kind=='technical' and session.day<state_starts[0] or kind=='v7' and session.day<state_starts[1]:continue
            result.append(PlannedUnit(Unit(key,kind,parents,config.query_memory_bytes),dict(args)))
        starts,keys=liquidity.setdefault(session.listing_key,([],[]))
        stamp=int(datetime.combine(session.day,time(4),ZoneInfo('America/New_York')).timestamp())
        starts.append(stamp);keys.append(aggregate)
        first=max(0,bisect_left(starts,stamp-max(config.volume_windows_seconds))-1)
        if len(keys)-first>1024:raise ValueError('Rolling activity context exceeds the 1024-parent publication bound')
        result.append(PlannedUnit(Unit(prefix+':activity','activity',tuple(keys[first:]),config.query_memory_bytes),
            dict(args,windows=config.volume_windows_seconds)))
        prior[session.listing_key]=(technical if session.day>=state_starts[0] else predecessors[0],
            v7 if session.day>=state_starts[1] else predecessors[1])
    return tuple(result)


def program_units(config,sessions,issuers,securities,*,prerequisites,source_parents,
                  universe_parent,seed_parents,corporate_parents,security_listings,
                  fundamental_start,price_start,short_start,state_start_dates=None,price_only_listings=(),reference_end=None):
    """Join acquisition/repair, shared market and reference work in one DAG.

    ``security_listings`` is the certified security-to-listing relationship,
    including historical renames. Price inputs reuse the market aggregates;
    callers must not supply a second independently calculated price graph.
    All prerequisites remain explicit certified units, including first-state
    seeds and source repair. This compiler performs no acquisition or writes.
    """
    from .execution import validate
    if any(type(d) is not date or d>config.start for d in (fundamental_start,price_start,short_start)):
        raise ValueError('Reference context ranges must include the target start')
    prerequisites=tuple(prerequisites);sessions=tuple(sessions)
    securities=tuple(securities)
    existing={p.unit.key:p for p in prerequisites}
    if len(existing)!=len(prerequisites):raise ValueError('Duplicate prerequisite keys')
    if universe_parent not in existing or existing[universe_parent].unit.product!='universe':
        raise ValueError('Certified universe prerequisite is missing')
    if set(security_listings)!={s.security_key for s in securities}:
        raise ValueError('Security/listing relationships do not match admitted securities')
    owners={}
    for security,listing_keys in security_listings.items():
        listing_keys=tuple(listing_keys)
        if not listing_keys or len(set(listing_keys))!=len(listing_keys):
            raise ValueError('Security listing history is empty or duplicated')
        for listing in listing_keys:
            if type(listing) is not int or not 0<listing<2**64 or listing in owners:
                raise ValueError('Listing has an invalid or competing security owner')
            owners[listing]=security
    if {s.listing_key for s in sessions}!=set(owners):
        raise ValueError('Market sessions do not cover the certified security/listing relationships')
    market=market_units(config,sessions,source_parents=source_parents,
        universe_parent=universe_parent,seed_parents=seed_parents,corporate_parents=corporate_parents,
        state_start_dates=state_start_dates,price_only_listings=price_only_listings)
    aggregates={s.security_key:[] for s in securities}
    for plan in market:
        if plan.unit.product=='market_aggregate' and date.fromisoformat(plan.arguments['day'])>=price_start:
            aggregates[owners[plan.arguments['listing_key']]].append(plan.unit.key)
    linked=[]
    for security in securities:
        shared=tuple(aggregates[security.security_key])
        if security.market_parents and set(security.market_parents)!=set(shared):
            raise ValueError('Security price inputs differ from the shared market aggregates')
        linked.append(replace(security,market_parents=shared))
    reference=reference_units(config,issuers,linked,prerequisites=(*prerequisites,*market),
        universe_parent=universe_parent,fundamental_start=fundamental_start,
        price_start=price_start,short_start=short_start,reference_end=reference_end)
    result=(*prerequisites,*market,*reference)
    validate(tuple(p.unit for p in result),config.global_memory_bytes)
    return result


@dataclass(frozen=True)
class IssuerInput:
    issuer_key: int
    cik: str
    source_hash: str
    source_parents: tuple[str,...]

    def __post_init__(self):
        import re
        if (type(self.issuer_key) is not int or not 0<self.issuer_key<2**64
                or not isinstance(self.cik,str) or not re.fullmatch(r'[0-9]{1,10}',self.cik) or int(self.cik)==0
                or not re.fullmatch('[0-9a-f]{64}',self.source_hash) or not self.source_parents):
            raise ValueError('Issuer requires a durable key, CIK and frozen acquisition evidence')


@dataclass(frozen=True)
class SecurityInput:
    security_key: int
    issuer_key: int
    market_parents: tuple[str,...]
    corporate_parent: str
    allocation_parent: str
    short_volume_hash: str
    short_interest_hash: str
    short_volume_parents: tuple[str,...]
    short_interest_parents: tuple[str,...]
    reported_supply_parent: str|None=None

    def __post_init__(self):
        import re
        if (any(type(k) is not int or not 0<k<2**64 for k in (self.security_key,self.issuer_key))
                or not isinstance(self.market_parents,tuple) or len(self.market_parents)>1000 or not self.corporate_parent or not self.allocation_parent
                or not self.short_volume_parents or not self.short_interest_parents
                or any(not re.fullmatch('[0-9a-f]{64}',h) for h in (self.short_volume_hash,self.short_interest_hash))):
            raise ValueError('Security requires price, corporate, allocation and original-public short dependencies')


def program_from_universe(config,snapshot,days,issuers,securities,*,prerequisites,source_parents,
                          universe_parent,seed_parents,corporate_parents,fundamental_start,
                          price_start,short_start,state_start_dates=None,maximum_sessions=250000,source_spellings=None):
    """Compile the joined graph directly from the frozen dated identity graph."""
    from .universe import execution_membership
    days=tuple(days)
    membership=execution_membership(snapshot,days,maximum_sessions=maximum_sessions)
    if not membership['ready']:raise ValueError('Historical execution membership is empty or contains partial market days')
    spellings={} if source_spellings is None else dict(source_spellings)
    if set(spellings)-{(s.listing_key,s.day) for s in membership['sessions']}:
        raise ValueError('Source spelling belongs to an unplanned listing/session')
    if spellings:
        from .contracts import digest
        membership=dict(membership,sessions=tuple(replace(s,source_ticker=spellings.get((s.listing_key,s.day)))
            for s in membership['sessions']),membership_hash=digest(dict(membership=membership['membership_hash'],
                spellings=[dict(listing=k[0],day=k[1],ticker=v) for k,v in sorted(spellings.items())])))
    prerequisites=tuple(prerequisites);securities=tuple(securities)
    authorities=[p for p in prerequisites if p.unit.key==universe_parent and p.unit.product=='universe']
    if len(authorities)!=1:raise ValueError('Frozen universe execution authority is missing or duplicated')
    args=authorities[0].arguments
    if (args.get('source_hash')!=snapshot['source_hash'] or date.fromisoformat(args['context_start'])>min(days)
            or date.fromisoformat(args['end'])<max(days)):
        raise ValueError('Universe prerequisite differs from the frozen membership source or range')
    if {s.security_key:s.issuer_key for s in securities}!=membership['security_issuers']:
        raise ValueError('Reference securities/issuers do not match the admitted dated universe')
    plans=program_units(config,membership['sessions'],issuers,securities,prerequisites=prerequisites,
        source_parents=source_parents,universe_parent=universe_parent,seed_parents=seed_parents,
        corporate_parents=corporate_parents,security_listings=membership['security_listings'],
        fundamental_start=fundamental_start,price_start=price_start,short_start=short_start,
        state_start_dates=state_start_dates)
    return dict(plans=plans,membership=membership)


def reference_units(config,issuers,securities,*,prerequisites,universe_parent,
                    fundamental_start,price_start,short_start,reference_end=None):
    """Build one issuer calculation shared by all its securities.

    Prerequisites include acquisition/certification units, shared market
    aggregates and external corporate/allocation authorities. Explicit context
    ranges come from requirement planning, not an arbitrary common lookback.
    The returned units join the market graph's single global executor.
    """
    reference_end=config.end if reference_end is None else reference_end
    if type(reference_end) is not date or not config.end<=reference_end<=config.end+timedelta(days=1):
        raise ValueError('Invalid reference availability coverage end')
    if any(type(d) is not date or d>config.start for d in (fundamental_start,price_start,short_start)):
        raise ValueError('Reference context ranges must include the target start')
    if price_start<date(2019,1,2) or (config.end-short_start).days>1000:
        raise ValueError('Price/short context exceeds the supported canonical or bounded publication range')
    if len(config.short_windows_sessions)>32 or max(config.short_windows_sessions)>1000:
        raise ValueError('Short-pressure windows exceed the supported bounded contract')
    prerequisites=tuple(prerequisites)
    prior={p.unit.key:p for p in prerequisites}
    if len(prior)!=len(prerequisites):raise ValueError('Duplicate prerequisite keys')
    if universe_parent not in prior or prior[universe_parent].unit.product!='universe':
        raise ValueError('Certified universe prerequisite is missing')
    issuers=tuple(sorted(issuers,key=lambda i:i.issuer_key));securities=tuple(sorted(securities,key=lambda s:s.security_key))
    if (not issuers or not securities or len({i.issuer_key for i in issuers})!=len(issuers)
            or len({s.security_key for s in securities})!=len(securities)):
        raise ValueError('Issuer/security inputs must be nonempty and unique')
    issuer_map={i.issuer_key:i for i in issuers};result=[]
    if len({int(i.cik) for i in issuers})!=len(issuers):raise ValueError('One SEC registrant maps to competing issuer keys')
    if {s.issuer_key for s in securities}!=set(issuer_map):
        raise ValueError('Every admitted security needs its exact issuer; unrelated issuers are not planned')
    def parents(keys,product=None):
        keys=tuple(keys)
        if len(set(keys))!=len(keys) or any(k not in prior for k in keys):
            raise ValueError('Reference prerequisite is missing or duplicated')
        if product and any(prior[k].unit.product!=product for k in keys):
            raise ValueError('Reference prerequisite product differs: '+product)
        for key in keys:unit_id(key)
        return keys
    def add(key,product,dependencies,args):
        if key in prior:raise ValueError('Reference unit collides with prerequisite')
        plan=PlannedUnit(Unit(key,product,tuple(dependencies),config.query_memory_bytes),args)
        result.append(plan);prior[key]=plan
        return key
    for issuer in issuers:
        common=dict(entity_key=issuer.issuer_key,start=str(fundamental_start),end=str(reference_end))
        reported=add(f'issuer:{issuer.issuer_key}:fundamentals','fundamentals',
            tuple(dict.fromkeys((*parents(issuer.source_parents,'sec_acquisition'),universe_parent))),
            dict(common,cik=issuer.cik,source_hash=issuer.source_hash))
        add(f'issuer:{issuer.issuer_key}:derived','derived_fundamentals',(reported,),dict(common))
    for security in securities:
        common=dict(entity_key=security.security_key,start=str(config.start),end=str(reference_end))
        if not security.market_parents:raise ValueError('Reference price dependencies require the shared market graph')
        price_parents=parents(security.market_parents,'market_aggregate');days=[]
        for key in price_parents:
            day=date.fromisoformat(prior[key].arguments['day'])
            if not price_start<=day<=config.end:raise ValueError('Price market parent lies outside its context range')
            days.append(day)
        if len(set(days))!=len(days):raise ValueError('Competing market listings for one security/session')
        prices=add(f'security:{security.security_key}:prices','regular_prices',price_parents,
            dict(common,start=str(price_start)))
        corporate=parents((security.corporate_parent,),'corporate_actions')[0]
        allocation=parents((security.allocation_parent,),'float_allocation')[0]
        external=[corporate,allocation]
        if security.reported_supply_parent:
            external+=parents((security.reported_supply_parent,),'reported_supply')
        for key in external:
            args=prior[key].arguments
            if (int(args.get('entity_key',0))!=security.security_key
                    or date.fromisoformat(args['start'])>config.start or date.fromisoformat(args['end'])<reference_end):
                raise ValueError('Float prerequisite identity or certified range differs')
        add(f'security:{security.security_key}:float','float_supply',
            (f'issuer:{security.issuer_key}:fundamentals',prices,*external),dict(common,issuer_key=security.issuer_key))
        for family,source_hash,inputs in (('short_volume',security.short_volume_hash,security.short_volume_parents),
                ('short_interest',security.short_interest_hash,security.short_interest_parents)):
            key=add(f'security:{security.security_key}:{family}',family,
                tuple(dict.fromkeys((*parents(inputs,family+'_acquisition'),universe_parent))),dict(common,start=str(short_start),source_hash=source_hash))
            if family=='short_volume':
                add(f'security:{security.security_key}:short_pressure','short_pressure',(key,),
                    dict(common,windows=config.short_windows_sessions))
    return tuple(result)
