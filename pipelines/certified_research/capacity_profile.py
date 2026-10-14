"""Bounded launch configuration for externally qualified pilot measurements.

Loading validates the profile and compatibility; it does not qualify a pilot.
The existing capacity controller independently checks current storage headroom.
"""
import json
from pathlib import Path

from . import capacity,installation,runtime_plan
from .contracts import digest


def load(path):
    path=Path(path)
    with path.open('rb') as stream:payload=stream.read(1024*1024+1)
    if len(payload)>1024*1024:raise ValueError('Capacity profile exceeds its metadata bound')
    def pairs(items):
        result={}
        for key,value in items:
            if key in result:raise ValueError('Duplicate capacity profile field: '+key)
            result[key]=value
        return result
    def reject_constant(value):raise ValueError('Nonfinite capacity profile number')
    value=json.loads(payload,object_pairs_hook=pairs,parse_constant=reject_constant)
    fields={'version','installation_hash','reference_contract_hash','rates','reserve_bytes','volume_groups','safety_factor','profile_hash'}
    if not isinstance(value,dict) or set(value)!=fields or value['version']!='qualified-capacity-profile-v1':
        raise ValueError('Capacity profile contract differs')
    if value['profile_hash']!=digest({k:v for k,v in value.items() if k!='profile_hash'}):
        raise ValueError('Capacity profile fingerprint differs')
    if not isinstance(value['rates'],list) or not 1<=len(value['rates'])<=64:
        raise ValueError('Capacity profile requires bounded qualified product rates')
    rates=tuple(capacity.PilotRate(**row) for row in value['rates'])
    if len({r.product for r in rates})!=len(rates):raise ValueError('Capacity profile product rates are duplicated')
    return dict(value,parsed_rates=rates)


def factory(query,profile):
    """Resolve compatibility from the frozen plan, never profile-supplied recipes."""
    def create(config,plans):
        if profile['profile_hash']!=digest({k:v for k,v in profile.items() if k not in ('profile_hash','parsed_rates')}):
            raise ValueError('Capacity profile changed after loading')
        if tuple(capacity.PilotRate(**row) for row in profile['rates'])!=profile['parsed_rates']:
            raise ValueError('Parsed capacity rates differ from their qualified profile')
        if profile['installation_hash']!=installation.manifest(config.database)['installation_hash']:
            raise ValueError('Capacity pilot installation differs from current normalized schemas')
        frozen=runtime_plan.resolve(config,plans)
        reference_hash=digest(runtime_plan.reference_contract())
        if profile['reference_contract_hash']!=reference_hash:
            raise ValueError('Capacity pilot reference algorithms differ from current frozen contracts')
        products=capacity.plan_demands(config,plans)['demands']
        recipe_hash=digest(dict(market_recipe=frozen['recipe_hash'],reference_contract=reference_hash))
        recipes={product:recipe_hash for product in products}
        return capacity.PlanCapacityGuard(query,config,profile['parsed_rates'],recipes,
            reserve_bytes=profile['reserve_bytes'],volume_groups=profile['volume_groups'],safety_factor=profile['safety_factor'])
    return create


def collection_guard(query,database,frozen,load_plan,profile):
    """Resolve child recipes independently; collection metadata has its own recipe."""
    from . import collection,collection_store
    create=factory(query,profile);recipes={}
    for member in frozen['members']:
        saved=load_plan(member['dataset_id'])
        guard=create(saved.config,saved.units)
        for product,hash_ in guard.recipes.items():
            if product in recipes and recipes[product]!=hash_:
                raise ValueError('Collection child storage recipes compete')
            recipes[product]=hash_
    recipes['collection_metadata']=digest(dict(version=collection.VERSION,
        ddl=tuple(t.ddl(database) for t in collection_store.tables())))
    return capacity.CollectionCapacityGuard(query,database,load_plan,profile['parsed_rates'],recipes,
        reserve_bytes=profile['reserve_bytes'],volume_groups=profile['volume_groups'],safety_factor=profile['safety_factor'])
