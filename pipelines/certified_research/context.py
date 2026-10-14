"""Dependency-derived context ranges; missing state is never guessed warmup."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date,datetime,time,timedelta,timezone
from zoneinfo import ZoneInfo

from .contracts import CampaignConfig,digest
from .source import exchange_sessions


@dataclass(frozen=True)
class StateSeed:
    session: date
    hash: str
    compatible: bool

    def __post_init__(self):
        if (type(self.session) is not date or type(self.compatible) is not bool or len(self.hash)!=64
                or any(c not in '0123456789abcdef' for c in self.hash)):
            raise ValueError('Invalid predecessor-state certificate')


def preceding_sessions(start,count,*,calendar=exchange_sessions):
    if type(start) is not date or type(count) is not int or count<0:
        raise ValueError('Invalid session context request')
    if count==0:return ()
    days=max(14,count*2+14)
    while days<=36600:
        sessions=calendar(start-timedelta(days=days),start-timedelta(days=1))
        if len(sessions)>=count:return tuple(sessions[-count:])
        days*=2
    raise ValueError('Context calendar cannot supply the required prior sessions')


def elapsed_sessions(first,seconds,*,calendar=exchange_sessions):
    """Prior complete market days covering the first PM rolling-window origin.

    Windows measure elapsed UTC seconds, including overnight/weekend closure.
    One earlier exchange session supplies an origin before the cutoff even
    when it falls on a closed day or before PM. No fixed session count is used.
    """
    from .activity import windows_contract
    windows_contract((seconds,))
    ny=ZoneInfo('America/New_York')
    opening=datetime.combine(first,time(4),ny).astimezone(timezone.utc)
    cutoff=(opening-timedelta(seconds=seconds)).astimezone(ny)
    # Include the cutoff date only when that session's PM starts before it.
    boundary=cutoff.date()+timedelta(days=1 if cutoff.time()>=time(4) else 0)
    origin=preceding_sessions(boundary,1,calendar=calendar)[0]
    return tuple(calendar(origin,first-timedelta(days=1))),cutoff.astimezone(timezone.utc)


def plan(config: CampaignConfig,*,indicator_seed: StateSeed|None=None,v7_seed: StateSeed|None=None,
         rebuild_origin: date|None=None,source_floor=date(2019,1,2),calendar=exchange_sessions):
    targets=calendar(config.start,config.end)
    if not targets:raise ValueError('Target contains no exchange sessions')
    first=targets[0];last=targets[-1]
    prior_market=preceding_sessions(first,1,calendar=calendar) # Current same-clock previous-session RVOL.
    elapsed,cutoff=elapsed_sessions(first,max(config.volume_windows_seconds),calendar=calendar)
    short=preceding_sessions(first,max(config.short_windows_sessions),calendar=calendar)
    demands=[dict(product='rvol',purpose='same-clock prior-session volume',sessions=prior_market),
             dict(product='rolling_activity',purpose='elapsed-second windows',seconds=max(config.volume_windows_seconds),
                  earliest_required_at=cutoff,sessions=elapsed),
             dict(product='short_pressure',purpose='publicly available rolling publication inputs',sessions=short)]
    blocked=[];market=set((*prior_market,*elapsed))
    if len(elapsed)+1>1024:
        blocked.append('Rolling activity context exceeds the 1024-parent publication bound')
    for name,seed in (('technical',indicator_seed),('v7',v7_seed)):
        if seed is not None and seed.compatible and seed.session<first:
            later=tuple(d for d in calendar(seed.session,first-timedelta(days=1)) if d>seed.session)
            market.update(later)
            demands.append(dict(product=name,purpose='compatible predecessor plus missing intervening sessions',
                                seed_session=seed.session,seed_hash=seed.hash,sessions=later,
                                calculation_start=later[0] if later else first))
        elif rebuild_origin is not None:
            if not source_floor<=rebuild_origin<=first or not calendar(rebuild_origin,rebuild_origin):
                raise ValueError('Declared rebuild origin is outside source bounds or not an exchange session')
            prefix=calendar(rebuild_origin,first-timedelta(days=1)) if rebuild_origin<first else ()
            market.update(prefix)
            demands.append(dict(product=name,purpose='explicit versioned rebuild prefix',origin=rebuild_origin,sessions=prefix,
                                calculation_start=rebuild_origin))
        else:
            blocked.append(name+': compatible prior state or explicit certified rebuild origin required')
    if market and min(market)<source_floor:
        blocked.append('Required market context precedes the canonical source floor')
    demands.append(dict(product='fundamentals',purpose='last original-public vintage plus required comparable periods and updates',
                        target_start=first,target_end=last))
    demands.append(dict(product='float',purpose='measurement-date prices (up to 7 days) and eligible SEC float periods (up to 520 days)',
                        measurement_price_days=7,sec_float_age_days=520))
    result=dict(targets=targets,market_context=tuple(sorted(market)),short_context=short,
                requirements=demands,blocked=tuple(blocked))
    result['hash']=digest(result)
    return result


def preparation_start(config,requested,*,rebuild_origin=None):
    """Definition-derived source dates; a missing state is never inferred warmup."""
    if type(requested) is not date or requested>config.start:raise ValueError('Invalid requested source context')
    demand=plan(config,rebuild_origin=rebuild_origin)
    fatal=[reason for reason in demand['blocked'] if 'compatible prior state' not in reason]
    if fatal:raise ValueError('; '.join(fatal))
    required=(*demand['market_context'],*demand['short_context'],*demand['targets'])
    start=min((requested,*required))
    if start<date(2019,1,2):raise ValueError('Source preparation context precedes canonical history')
    return start
