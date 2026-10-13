"""Read-only launch preparation joining seed discovery and required context."""
from dataclasses import replace
from datetime import date
from threading import Event
from itertools import islice

from . import context,indicator_state,v7
from .campaign import PlannedUnit
from .contracts import digest
from .execution import Unit
from .recipe import verify_definition


def prepare_listing(query,config,claim,source_guard,admitted_sessions,recipe_definition,rules,
                    day_sources,adjustment_hashes,*,indicator_dataset=None,v7_dataset=None,
                    stop=None,maximum_candidates=128,maximum_sessions=4096):
    """Prepare one listing's lane; the global launcher combines returned units.

    Sessions must come from certified execution membership and include the
    known historical context. Source/adjustment hashes are independently frozen
    authorities. No genesis, rename transition or missing field is inferred.
    Source roots returned as dependencies are installed by certified_units().
    """
    stop=stop if stop is not None else Event()
    if stop.is_set():raise InterruptedError('Seed preparation stopped')
    if type(maximum_sessions) is not int or not 1<=maximum_sessions<=65536:
        raise ValueError('Invalid listing preparation metadata bound')
    supplied=tuple(islice(admitted_sessions,maximum_sessions+1))
    if len(supplied)>maximum_sessions:raise ValueError('Listing preparation exceeds session metadata bound')
    sessions=tuple(sorted(supplied,key=lambda s:s.day))
    if (not sessions or len({s.listing_key for s in sessions})!=1
            or len({s.day for s in sessions})!=len(sessions)):
        raise ValueError('Seed preparation needs one unambiguous admitted listing history')
    target=tuple(s for s in sessions if config.start<=s.day<=config.end)
    if not target:raise ValueError('Seed preparation has no admitted target sessions')
    listing=target[0].listing_key;first=target[0].day
    rules=tuple(rules);recipe_hash=verify_definition(recipe_definition,rules)
    sources={date.fromisoformat(str(d)):h for d,h in day_sources.items()}
    adjustments={date.fromisoformat(str(d)):h for d,h in adjustment_hashes.items()}
    if len(sources)!=len(day_sources) or len(adjustments)!=len(adjustment_hashes):
        raise ValueError('Duplicated frozen source or adjustment dates')
    def guard():
        if stop.is_set():raise InterruptedError('Seed preparation stopped')
        claim.check();source_guard()
    guard()
    technical=indicator_state.discover(query,config.database,claim,listing,first,recipe_hash,digest(rules),
        sources,dataset_id=indicator_dataset)
    guard()
    prior={s.day:dict(source_hash=sources[s.day],ticker=s.ticker,adjustment_hash=adjustments[s.day])
        for s in sessions if s.day<first and s.day in sources and s.day in adjustments}
    structural=v7.discover(query,config.database,claim,listing,first,recipe_hash,rules,prior,
        dataset_id=v7_dataset,maximum_candidates=maximum_candidates) if prior else None
    guard()
    lane=replace(config,start=first,end=target[-1].day)
    demand=context.plan(lane,
        indicator_seed=context.StateSeed(technical['session'],technical['dependency']['output_hash'],True) if technical else None,
        v7_seed=context.StateSeed(structural['session'],structural['checkpoint']['state_hash'],True) if structural else None)
    blocked=list(demand['blocked']);by_day={s.day:s for s in sessions}
    needed=tuple(sorted(set((*demand['targets'],*demand['market_context']))))
    missing_membership=tuple(d for d in needed if d not in by_day)
    missing_source=tuple(d for d in needed if d not in sources)
    if missing_membership:blocked.append('Required dated membership is missing for '+str(len(missing_membership))+' market sessions')
    if missing_source:blocked.append('Required canonical/reporting source is missing for '+str(len(missing_source))+' market sessions')
    # Current rollover does not have a certified rename bridge. Surface this
    # during preparation rather than after market aggregates have been written.
    if structural and structural['dependency']['state_context']['ticker']!=target[0].ticker:
        blocked.append('V7 seed requires a certified historical rename transition')
    roots=[];parents=[];starts=[];seed_days=[]
    for family,found in (('technical',technical),('v7',structural)):
        if found is None:continue
        day=found['session'];seed_days.append(day)
        key=f'seed:{listing}:{family}'
        roots.append(PlannedUnit(Unit(key,family+'_seed',('source:'+str(day),),config.query_memory_bytes),
            dict(dependency=found['dependency'])))
        parents.append(key)
        starts.append(next(r['calculation_start'] for r in demand['requirements'] if r['product']==family))
    ready=not blocked
    result=dict(ready=ready,listing_key=listing,recipe_hash=recipe_hash,blocked=tuple(blocked),
        missing_membership=missing_membership,missing_source=missing_source,context=demand,
        required_source_days=tuple(sorted(set((*needed,*seed_days)))),seed_units=tuple(roots),
        seed_parents={listing:tuple(parents)} if ready else {},
        state_start_dates={listing:tuple(starts)} if ready else {},
        sessions=tuple(by_day[d] for d in needed if d in by_day))
    result['preparation_hash']=digest(dict(recipe=recipe_hash,config=config.identity(),listing=listing,
        context=demand,blocked=blocked,roots=[dict(key=p.unit.key,parents=p.unit.parents,arguments=p.arguments) for p in roots],
        sessions=[dict(day=s.day,listing_key=s.listing_key,ticker=s.ticker) for s in result['sessions']],
        sources={str(d):sources.get(d) for d in result['required_source_days']}))
    guard();return result
