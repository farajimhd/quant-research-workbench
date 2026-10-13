"""Execution dependencies for certified market sessions, without nested pools."""
from __future__ import annotations

from dataclasses import dataclass,replace
from datetime import date,datetime,time
from bisect import bisect_left
from zoneinfo import ZoneInfo

from .campaign import PlannedUnit,unit_id
from .contracts import CampaignConfig
from .execution import Unit


@dataclass(frozen=True)
class MarketSession:
    day: date
    listing_key: int
    ticker: str

    def __post_init__(self):
        if (type(self.day) is not date or type(self.listing_key) is not int
                or not 0<self.listing_key<2**64 or not isinstance(self.ticker,str)
                or not self.ticker or len(self.ticker.encode())>64):
            raise ValueError('Invalid dated market session')


def market_units(config: CampaignConfig,sessions,*,source_parents,universe_parent,seed_parents,corporate_parents):
    """Compile source/repair→aggregate→activity,indicators,V7 dependencies.

    Callers supply only admitted dated sessions and already planned source,
    universe and certified-context units. A source parent means acquisition
    AND reporting repair have verified; it must not be merely a download.
    First-session seed parents may certify an explicit genesis, but are never
    omitted. This function does not infer eligibility or manufacture context.
    """
    sessions=tuple(sorted(sessions,key=lambda s:(s.listing_key,s.day)))
    if not sessions:raise ValueError('No admitted market sessions')
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
        dependencies=tuple(dict.fromkeys((*sources,universe_parent)))
        for key,kind,parents in ((aggregate,'market_aggregate',dependencies),
                (technical,'technical',(aggregate,predecessors[0],corporate)),(v7,'v7',(aggregate,predecessors[1],corporate))):
            result.append(PlannedUnit(Unit(key,kind,parents,config.query_memory_bytes),dict(args)))
        starts,keys=liquidity.setdefault(session.listing_key,([],[]))
        stamp=int(datetime.combine(session.day,time(4),ZoneInfo('America/New_York')).timestamp())
        starts.append(stamp);keys.append(aggregate)
        first=max(0,bisect_left(starts,stamp-max(config.volume_windows_seconds))-1)
        result.append(PlannedUnit(Unit(prefix+':activity','activity',tuple(keys[first:]),config.query_memory_bytes),
            dict(args,windows=config.volume_windows_seconds)))
        prior[session.listing_key]=(technical,v7)
    return tuple(result)


def program_units(config,sessions,issuers,securities,*,prerequisites,source_parents,
                  universe_parent,seed_parents,corporate_parents,security_listings,
                  fundamental_start,price_start,short_start):
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
        universe_parent=universe_parent,seed_parents=seed_parents,corporate_parents=corporate_parents)
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
        price_start=price_start,short_start=short_start)
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
                or not 1<=len(self.market_parents)<=1000 or not self.corporate_parent or not self.allocation_parent
                or not self.short_volume_parents or not self.short_interest_parents
                or any(not re.fullmatch('[0-9a-f]{64}',h) for h in (self.short_volume_hash,self.short_interest_hash))):
            raise ValueError('Security requires price, corporate, allocation and original-public short dependencies')


def reference_units(config,issuers,securities,*,prerequisites,universe_parent,
                    fundamental_start,price_start,short_start):
    """Build one issuer calculation shared by all its securities.

    Prerequisites include acquisition/certification units, shared market
    aggregates and external corporate/allocation authorities. Explicit context
    ranges come from requirement planning, not an arbitrary common lookback.
    The returned units join the market graph's single global executor.
    """
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
        common=dict(entity_key=issuer.issuer_key,start=str(fundamental_start),end=str(config.end))
        reported=add(f'issuer:{issuer.issuer_key}:fundamentals','fundamentals',
            tuple(dict.fromkeys((*parents(issuer.source_parents,'sec_acquisition'),universe_parent))),
            dict(common,cik=issuer.cik,source_hash=issuer.source_hash))
        add(f'issuer:{issuer.issuer_key}:derived','derived_fundamentals',(reported,),dict(common))
    for security in securities:
        common=dict(entity_key=security.security_key,start=str(config.start),end=str(config.end))
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
                    or date.fromisoformat(args['start'])>config.start or date.fromisoformat(args['end'])<config.end):
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
