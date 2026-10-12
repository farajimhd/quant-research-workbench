"""Dependency-derived context ranges; missing state is never guessed warmup."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date,timedelta

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


def plan(config: CampaignConfig,*,indicator_seed: StateSeed|None=None,v7_seed: StateSeed|None=None,
         rebuild_origin: date|None=None,source_floor=date(2019,1,2),calendar=exchange_sessions):
    targets=calendar(config.start,config.end)
    if not targets:raise ValueError('Target contains no exchange sessions')
    first=targets[0];last=targets[-1]
    prior_market=preceding_sessions(first,1,calendar=calendar) # Current same-clock previous-session RVOL.
    short=preceding_sessions(first,max(config.short_windows_sessions),calendar=calendar)
    demands=[dict(product='rvol',purpose='same-clock prior-session volume',sessions=prior_market),
             dict(product='rolling_activity',purpose='elapsed-second windows',seconds=max(config.volume_windows_seconds)),
             dict(product='short_pressure',purpose='publicly available rolling publication inputs',sessions=short)]
    blocked=[];market=set(prior_market)
    for name,seed in (('technical',indicator_seed),('v7',v7_seed)):
        if seed is not None and seed.compatible and seed.session<first:
            later=tuple(d for d in calendar(seed.session,first-timedelta(days=1)) if d>seed.session)
            market.update(later)
            demands.append(dict(product=name,purpose='compatible predecessor plus missing intervening sessions',
                                seed_session=seed.session,seed_hash=seed.hash,sessions=later))
        elif rebuild_origin is not None:
            if not source_floor<=rebuild_origin<first:
                raise ValueError('Declared rebuild origin is outside source bounds or not prior to target')
            prefix=calendar(rebuild_origin,first-timedelta(days=1));market.update(prefix)
            demands.append(dict(product=name,purpose='explicit versioned rebuild prefix',origin=rebuild_origin,sessions=prefix))
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
