"""Read-only first-launch preparation feeding the shared executable assembler."""
from datetime import date
from threading import Event,Lock

from . import context,float_data,initial_plan,planning,recipe,sec_coverage,seed_preparation,source,universe,cohorts
from .campaign import PlannedUnit
from .contracts import digest
from .execution import Unit,execute


def discover_and_prepare(query,config,dataset_id,claim,client_factory,source_guard,canonical,reporting,definition,rules,*,
                         fundamental_start,short_start,anchor_date,adjustment_hashes=None,
                         indicator_datasets=None,v7_datasets=None,stop=None,progress=None,rebuild_origin=None,source_preparer=None):
    """Compose reference discovery and preparation without caller-built locators."""
    from . import reference_discovery
    stop=Event() if stop is None else stop
    claim.check();source_guard()
    if stop.is_set():raise InterruptedError('Initial discovery stopped')
    source.require_ready(query,canonical,reporting)
    start=min((*canonical['expected'],short_start))
    snapshot=cohorts.select(universe.load(query,start,config.end,anchor_date),config.issuer_keys)
    references=reference_discovery.discover(query,config,dataset_id,claim,client_factory,source_guard,snapshot,
        canonical['expected'],fundamental_start=fundamental_start,short_start=short_start,stop=stop,progress=progress)
    if not references['ready']:
        return dict(ready=False,phase='reference-authorities',missing_authorities=references['missing_authorities'])
    if adjustment_hashes is None:
        adjustment_hashes=seed_preparation.discover_adjustment_contexts(query,config,dataset_id,claim,
            source_guard,references['membership'],references['prerequisites'],references['corporate_parents'],
            canonical['expected'],stop=stop,snapshot=snapshot)
    authority=PlannedUnit(Unit('universe','universe'),dict(context_start=str(start),end=str(config.end),
        anchor_date=str(anchor_date),source_hash=snapshot['source_hash']))
    if config.issuer_keys:authority=PlannedUnit(authority.unit,dict(authority.arguments,issuer_keys=config.issuer_keys))
    assembled=prepare(query,config,claim,client_factory,source_guard,canonical,reporting,definition,rules,
        references['issuers'],references['securities'],prerequisites=(authority,*references['prerequisites']),
        universe_parent='universe',corporate_parents=references['corporate_parents'],adjustment_hashes=adjustment_hashes,
        fundamental_start=fundamental_start,short_start=short_start,anchor_date=anchor_date,
        indicator_datasets=indicator_datasets,v7_datasets=v7_datasets,stop=stop,progress=progress,rebuild_origin=rebuild_origin,source_preparer=source_preparer)
    return dict(assembled,ready=True)


def prepare(query,config,claim,client_factory,source_guard,canonical,reporting,definition,rules,
            issuers,securities,*,prerequisites,universe_parent,corporate_parents,adjustment_hashes,
            fundamental_start,short_start,anchor_date,indicator_datasets=None,v7_datasets=None,
            stop=None,progress=None,maximum_sessions=250000,maximum_issuers=8192,rebuild_origin=None,source_preparer=None):
    """Discover float prices and state context before installing any plan.

    Reference source locators must already be certified; their discovery and
    acquisition are separate gates. This coordinator replaces manually prepared
    listing sessions, price-only histories and seed units with one read-only
    preparation path. A rebuild origin must be explicitly supplied; missing
    genesis or rename evidence is never inferred.
    """
    stop=Event() if stop is None else stop
    issuers=tuple(issuers);securities=tuple(securities);prerequisites=tuple(prerequisites)
    if (type(maximum_issuers) is not int or not 1<=maximum_issuers<=65536
            or not issuers or len(issuers)>maximum_issuers or len({i.issuer_key for i in issuers})!=len(issuers)):
        raise ValueError('Initial preparation requires unique issuer inputs')
    if type(maximum_sessions) is not int or not 1<=maximum_sessions<=1000000:
        raise ValueError('Invalid initial preparation metadata bound')
    by_key={p.unit.key:p for p in prerequisites}
    if len(by_key)!=len(prerequisites):raise ValueError('Initial prerequisites are duplicated')
    authority=by_key.get(universe_parent)
    if authority is None or authority.unit.product!='universe':raise ValueError('Initial universe prerequisite is missing')
    recipe.verify_definition(definition,rules)
    targets=source.exchange_sessions(config.start,config.end)
    if not targets:raise ValueError('Initial preparation has no target exchange sessions')
    short_context=context.preceding_sessions(targets[0],max(config.short_windows_sessions))
    if type(short_start) is not date or short_start>min((*short_context,*targets)):
        raise ValueError('Short source range omits requirement-derived prior sessions')
    reference_end=planning.reference_coverage_end(config.end)
    sec_coverage.require_ready(query,fundamental_start,reference_end)
    original_epoch=source.require_ready(query,canonical,reporting)
    def guard():
        if stop.is_set():raise InterruptedError('Initial preparation stopped')
        claim.check();source_guard()
    guard()
    for issuer in issuers:
        if len(issuer.source_parents)!=1:raise ValueError('Initial issuer requires one SEC source locator')
        parent=by_key.get(issuer.source_parents[0]);args={} if parent is None else parent.arguments
        if (parent is None or parent.unit.product!='sec_acquisition' or args.get('entity_key')!=issuer.issuer_key
                or args.get('source_hash')!=issuer.source_hash or str(args.get('cik','')).lstrip('0')!=issuer.cik.lstrip('0')
                or date.fromisoformat(args['start'])>fundamental_start or date.fromisoformat(args['end'])<reference_end):
            raise ValueError('Initial SEC input scope differs from issuer requirements')
    issuer_map={str(i.issuer_key):i for i in issuers}
    def float_context(unit,parents,signal):
        guard();issuer=issuer_map[unit.key];args=by_key[issuer.source_parents[0]].arguments
        client=client_factory()
        try:
            result=float_data.source_price_sessions(client.query,claim,guard,issuer.issuer_key,issuer.cik,
                date.fromisoformat(args['start']),date.fromisoformat(args['end']),config.start,reference_end,issuer.source_hash,
                measurement_end=config.end)
            guard();return result
        finally:client.close()
    def report(change):
        if progress is None:return
        change=dict(change,phase='float-price-context')
        change['counts']=dict(change['counts'])
        change['counts']['prepared']=change['counts'].pop('certified',0)+change['counts'].pop('reused',0)
        if change['status'] in ('certified','reused'):change['status']='prepared'
        progress(change)
    jobs=tuple(Unit(str(i.issuer_key),'float_price_context',memory_bytes=config.query_memory_bytes) for i in issuers)
    result=execute(jobs,{'float_price_context':float_context},workers=config.workers,
        memory_bytes=config.global_memory_bytes,stop=stop,progress=report)
    if result['status']!='complete':
        if stop.is_set():raise InterruptedError('Initial float-price preparation stopped')
        errors=result['errors']
        raise ValueError(f'Initial float-price preparation failed ({len(errors)} issuers): '+
            str({k:str(errors[k])[:256] for k in sorted(errors)[:8]}))
    contexts={int(k):v for k,v in result['results'].items()}
    price_days=planning.price_context_days(targets,contexts)
    required=tuple(sorted(set((*canonical['expected'],*price_days))))
    extra=tuple(day for day in required if day not in canonical['expected'])
    if extra and source_preparer is not None:
        guard();source_preparer(required);guard()
    frozen=source.inventory(query,min(required),max(required),sessions=required)
    frozen_reporting=source.reporting_inventory(query,frozen)
    epoch=source.require_ready(query,frozen,frozen_reporting)
    source_lock=Lock()
    def frozen_guard():
        guard()
        with source_lock:
            if source.require_ready(query,frozen,frozen_reporting)!=epoch:
                raise ValueError('Initial canonical/reporting source changed during preparation')
    frozen_guard()
    membership_start=min((*required,short_start))
    snapshot=cohorts.select(universe.load(query,membership_start,config.end,anchor_date),config.issuer_keys)
    if not snapshot['ready']:raise ValueError('Initial historical universe requires evidence repair: '+str(snapshot['rejection_counts']))
    membership=universe.execution_membership(snapshot,required,maximum_sessions=maximum_sessions)
    if not membership['ready']:raise ValueError('Initial dated membership is empty or partial')
    owners={listing:security for security,listings in membership['security_listings'].items() for listing in listings}
    target_securities={owners[s.listing_key] for s in membership['sessions'] if config.start<=s.day<=config.end}
    if {s.security_key:s.issuer_key for s in securities}!={k:membership['security_issuers'][k] for k in target_securities}:
        raise ValueError('Initial reference securities differ from target dated membership')
    if {i.issuer_key for i in issuers}!={membership['security_issuers'][k] for k in target_securities}:
        raise ValueError('Initial issuer inputs differ from target dated membership')
    membership=dict(membership,sessions=tuple(s for s in membership['sessions'] if owners[s.listing_key] in target_securities),
        security_listings={k:v for k,v in membership['security_listings'].items() if k in target_securities},
        security_issuers={k:v for k,v in membership['security_issuers'].items() if k in target_securities},
        membership_hash=digest(dict(base_membership=membership['membership_hash'],target_securities=tuple(sorted(target_securities)))))
    assigned=planning.assign_float_price_sessions(membership,contexts)
    target_listings={s.listing_key for s in membership['sessions'] if config.start<=s.day<=config.end}
    price_requests={k:v for k,v in assigned['price_sessions'].items() if k in target_listings}
    prepared=seed_preparation.prepare_universe(client_factory,config,claim,frozen_guard,membership,definition,rules,
        source.market_day_hashes(frozen,frozen_reporting),adjustment_hashes,indicator_datasets=indicator_datasets,
        v7_datasets=v7_datasets,stop=stop,progress=progress,price_sessions=price_requests,
        rebuild_origin=rebuild_origin,universe_parent=universe_parent)
    if not prepared['ready']:raise ValueError('Initial market preparation is blocked: '+str(prepared['states']))
    price_only=tuple(s for s in membership['sessions'] if s.listing_key not in target_listings
        and s.day in assigned['price_sessions'].get(s.listing_key,()))
    from .source import resolve_spellings
    price_only=resolve_spellings(query,price_only,guard=frozen_guard)['sessions'] if price_only else ()
    sessions=(*prepared['sessions'],*price_only)
    planned_listings={s.listing_key for s in sessions}
    linked={k:tuple(listing for listing in v if listing in planned_listings) for k,v in membership['security_listings'].items()}
    universe_unit=PlannedUnit(authority.unit,dict(context_start=str(membership_start),end=str(config.end),
        anchor_date=str(anchor_date),source_hash=snapshot['source_hash']))
    if config.issuer_keys:universe_unit=PlannedUnit(universe_unit.unit,dict(universe_unit.arguments,issuer_keys=config.issuer_keys))
    prerequisites=tuple(universe_unit if p.unit.key==universe_parent else p for p in prerequisites)
    assembled=initial_plan.assemble(config,prepared,frozen,frozen_reporting,definition,rules,issuers,securities,
        prerequisites=prerequisites,universe_parent=universe_parent,corporate_parents=corporate_parents,
        security_listings=linked,fundamental_start=fundamental_start,price_start=min(s.day for s in sessions),
        short_start=short_start,price_context_sessions=price_only)
    frozen_guard()
    current=cohorts.select(universe.load(query,membership_start,config.end,anchor_date),config.issuer_keys)
    if not current['ready'] or current['source_hash']!=snapshot['source_hash']:
        raise ValueError('Initial Gateway identity source changed during preparation')
    assembled.update(float_contexts=contexts,float_price_unavailable=assigned['unavailable'],
        original_source_epoch=original_epoch,preparation_hash=digest(dict(market=prepared['preparation_hash'],
        prices=assigned['assignment_hash'],membership=membership['membership_hash'],source=epoch)))
    return assembled
