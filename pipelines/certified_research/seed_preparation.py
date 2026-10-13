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
    if len({by_day[d].ticker for d in needed if d in by_day})>1:
        blocked.append('Market context requires a certified historical rename transition')
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


def prepare_universe(client_factory,config,claim,source_guard,membership,recipe_definition,rules,
                     day_sources,adjustment_hashes,*,indicator_datasets=None,v7_datasets=None,
                     stop=None,progress=None,maximum_listings=8192,maximum_candidates=128,
                     maximum_sessions=4096):
    """One bounded preparation pool; no nested pools and no database writers.

    Independent listing preparations have private clients. A blocked listing
    prevents executable combined output; diagnostics retain every outcome.
    This phase prepares metadata, not a dataset certification seal.
    """
    from .execution import execute
    from threading import Lock
    if not membership.get('ready'):raise ValueError('Universe preparation requires admitted execution membership')
    if type(maximum_listings) is not int or not 1<=maximum_listings<=65536:
        raise ValueError('Invalid universe preparation listing bound')
    grouped={}
    targets={s.listing_key for s in membership['sessions'] if config.start<=s.day<=config.end}
    if not targets or len(targets)>maximum_listings:raise ValueError('Universe preparation target is empty or exceeds listing bound')
    for session in membership['sessions']:
        if session.listing_key in targets:grouped.setdefault(session.listing_key,[]).append(session)
    indicator_datasets=indicator_datasets or {};v7_datasets=v7_datasets or {}
    for selection in (adjustment_hashes,indicator_datasets,v7_datasets):
        if set(selection)-targets:raise ValueError('Preparation input belongs to an unplanned listing')
    stop=stop if stop is not None else Event();blocked={};lock=Lock();reported_blocked=set()
    class Blocked(Exception):pass
    def run(unit,parents,signal):
        listing=int(unit.key);client=client_factory()
        try:
            value=prepare_listing(client.query,config,claim,source_guard,grouped[listing],recipe_definition,rules,
                day_sources,adjustment_hashes.get(listing,{}),indicator_dataset=indicator_datasets.get(listing),
                v7_dataset=v7_datasets.get(listing),stop=signal,maximum_candidates=maximum_candidates,
                maximum_sessions=maximum_sessions)
            if not value['ready']:
                with lock:blocked[unit.key]=value
                raise Blocked('; '.join(value['blocked']))
            return value
        finally:client.close()
    def event(change):
        if progress is None:return
        labels={'certified':'prepared','reused':'prepared'}
        value=dict(change,status=labels.get(change['status'],change['status']))
        if change['status']=='failed' and change['unit'] in blocked:
            value['status']='blocked';reported_blocked.add(change['unit'])
            value['reason']='; '.join(blocked[change['unit']]['blocked'])[:256]
        counts=change['counts']
        value['counts']={k:v for k,v in counts.items() if k not in ('certified','reused')}
        value['counts']['prepared']=counts['certified']+counts['reused']
        value['counts']['failed']=counts['failed']-len(reported_blocked)
        value['counts']['blocked']=counts['blocked']+len(reported_blocked)
        progress(value)
    units=tuple(Unit(str(key),'seed_prepare',memory_bytes=config.query_memory_bytes) for key in sorted(grouped))
    result=execute(units,{'seed_prepare':run},workers=config.workers,memory_bytes=config.global_memory_bytes,
        stop=stop,progress=event)
    ready=result['status']=='complete';prepared={int(k):v for k,v in result['results'].items()}
    if ready:
        claim.check();source_guard()
        if len({v['recipe_hash'] for v in prepared.values()})!=1:
            raise ValueError('Prepared listings use competing market recipes')
    failed={int(k):str(error)[:256] for k,error in result['errors'].items() if k not in blocked}
    seeds=tuple(p for key in sorted(prepared) for p in prepared[key]['seed_units']) if ready else ()
    parents={key:value['seed_parents'][key] for key,value in prepared.items()} if ready else {}
    starts={key:value['state_start_dates'][key] for key,value in prepared.items()} if ready else {}
    output=dict(ready=ready,status='ready' if ready else 'interrupted' if result['status']=='interrupted' else 'blocked',
        prepared=prepared,blocked={int(k):v for k,v in blocked.items()},failed=failed,
        states={int(k):('prepared' if v=='certified' else 'blocked' if k in blocked else v) for k,v in result['units'].items()},
        seed_units=seeds,seed_parents=parents,state_start_dates=starts,
        sessions=tuple(s for key in sorted(prepared) for s in prepared[key]['sessions']) if ready else (),
        required_source_days=tuple(sorted({d for v in (*prepared.values(),*blocked.values()) for d in v['required_source_days']})))
    output['preparation_hash']=digest(dict(config=config.identity(),membership=membership['membership_hash'],
        prepared={str(k):v['preparation_hash'] for k,v in sorted(prepared.items())},
        blocked={k:v['preparation_hash'] for k,v in sorted(blocked.items())},failed=failed,states=output['states']))
    return output
