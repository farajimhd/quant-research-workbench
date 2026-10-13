"""Join prepared lanes and certified sources into one executable plan graph."""
from dataclasses import replace

from . import planning,runtime_plan,source
from .contracts import digest
from .execution import validate


def run(query,config,dataset_id,assembled,claim,client_factory,dictionary_claim_factory,
        capacity_factory,*,stop=None,progress=None):
    """Publish and execute through the same admission gates used by resume.

    No initial metadata is installed until ExecutionLaunch.run admits the
    complete graph. Client and capacity factories receive frozen resources.
    """
    from types import SimpleNamespace
    from uuid import UUID
    from . import verification
    from .launch import ExecutionLaunch
    UUID(dataset_id)
    if not callable(capacity_factory):raise ValueError('Initial execution requires qualified capacity admission')
    plans=tuple(assembled['plans'])
    plan_fingerprint=digest(tuple(p.row(dataset_id) for p in plans))
    frozen=runtime_plan.resolve(config,plans)
    if frozen['recipe_hash']!=assembled['recipe_hash']:
        raise ValueError('Initial execution recipe differs from assembled plan')
    saved=SimpleNamespace(dataset_id=dataset_id,config=config,units=plans,source_hash=assembled['source_hash'])
    claim.check()
    guard=verification.source_guard(query,saved)
    guard()
    clients=lambda:client_factory(config)
    capacity=capacity_factory(config,plans)
    if not callable(capacity):raise ValueError('Capacity factory must return a qualified admission guard')
    runtime=runtime_plan.runtime(saved,claim,clients,guard)
    if digest(tuple(p.row(dataset_id) for p in plans))!=plan_fingerprint:
        raise ValueError('Initial plan changed during execution assembly')
    launch=ExecutionLaunch(query,config,dataset_id,plans,saved.source_hash,claim,clients,guard,
        capacity,dictionary_claim_factory,runtime)
    return launch.run(stop=stop,progress=progress)


def assemble(config,prepared,canonical,reporting,definition,rules,issuers,securities,*,
             prerequisites,universe_parent,corporate_parents,security_listings,
             fundamental_start,price_start,short_start):
    """No reads or writes; acquisition/repair must finish before this freeze.

    Metadata, source roots, state seeds, market consumers and reference products
    share one dependency graph. Missing preparation or coverage cannot produce
    a partial executable plan. Production admission remains a separate gate.
    """
    if not prepared.get('ready') or prepared.get('blocked') or prepared.get('failed'):
        raise ValueError('Initial plan requires complete market preparation')
    metadata=runtime_plan.units(config,definition,rules)
    frozen=runtime_plan.resolve(config,metadata)
    lanes=prepared.get('prepared',{})
    if not lanes or any(p['recipe_hash']!=frozen['recipe_hash'] for p in lanes.values()):
        raise ValueError('Prepared market recipe differs from initial plan')
    roots,source_parents=source.certified_units(canonical,reporting)
    required=set(prepared['required_source_days'])
    if required-set(source_parents):
        raise ValueError('Initial plan lacks required canonical context or seed source days')
    plans=planning.program_units(config,prepared['sessions'],issuers,securities,
        prerequisites=(*tuple(prerequisites),*metadata,*roots,*prepared['seed_units']),
        source_parents=source_parents,universe_parent=universe_parent,
        seed_parents=prepared['seed_parents'],corporate_parents=corporate_parents,
        security_listings=security_listings,fundamental_start=fundamental_start,
        price_start=price_start,short_start=short_start,state_start_dates=prepared['state_start_dates'])
    # Validate the frozen recipe before any aggregate consumer is scheduled.
    plans=tuple(replace(p,unit=replace(p.unit,parents=(*p.unit.parents,'calculation_recipe')))
        if p.unit.product=='market_aggregate' else p for p in plans)
    validate(tuple(p.unit for p in plans),config.global_memory_bytes)
    return dict(plans=plans,source_hash=digest(dict(source=canonical['content_hash'],reporting=reporting['content_hash'])),
        recipe_hash=frozen['recipe_hash'],preparation_hash=prepared['preparation_hash'])
