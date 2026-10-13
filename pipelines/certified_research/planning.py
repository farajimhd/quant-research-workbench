"""Execution dependencies for certified market sessions, without nested pools."""
from __future__ import annotations

from dataclasses import dataclass
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


def market_units(config: CampaignConfig,sessions,*,source_parents,universe_parent,seed_parents):
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
        prefix=f'market:{session.listing_key}:{session.day}'
        aggregate=prefix+':aggregate';technical=prefix+':technical';v7=prefix+':v7'
        args=dict(day=str(session.day),listing_key=session.listing_key,ticker=session.ticker)
        dependencies=tuple(dict.fromkeys((*sources,universe_parent)))
        for key,kind,parents in ((aggregate,'market_aggregate',dependencies),
                (technical,'technical',(aggregate,predecessors[0])),(v7,'v7',(aggregate,predecessors[1]))):
            result.append(PlannedUnit(Unit(key,kind,parents,config.query_memory_bytes),dict(args)))
        starts,keys=liquidity.setdefault(session.listing_key,([],[]))
        stamp=int(datetime.combine(session.day,time(4),ZoneInfo('America/New_York')).timestamp())
        starts.append(stamp);keys.append(aggregate)
        first=max(0,bisect_left(starts,stamp-max(config.volume_windows_seconds))-1)
        result.append(PlannedUnit(Unit(prefix+':activity','activity',tuple(keys[first:]),config.query_memory_bytes),
            dict(args,windows=config.volume_windows_seconds)))
        prior[session.listing_key]=(technical,v7)
    return tuple(result)
