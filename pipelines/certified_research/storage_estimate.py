"""Readonly stored-plan storage estimates; never qualifies pilot measurements."""
from . import campaign,capacity,capacity_profile,collection_store
from .contracts import digest
from dataclasses import replace


class ReadSnapshot:
    def check(self):pass # Readonly inspection rechecks immutable definitions afterward.


def estimate(query,database,dataset_id,*,profile=None,is_collection=False):
    if is_collection:
        store=collection_store.Store(query,database,dataset_id,ReadSnapshot())
        frozen=store.load()
        def load(id_):return campaign.load_plan(query,database,id_)
        if profile is not None:
            result=capacity_profile.collection_guard(query,database,frozen,load,profile)(frozen)
        else:
            demands={};active={};unknown={};members=[]
            for member in frozen['members']:
                saved=load(member['dataset_id'])
                if (saved.dataset_id!=member['dataset_id'] or saved.plan_hash!=member['plan_hash']
                        or saved.config.database!=database or tuple(saved.config.issuer_keys)!=tuple(member['issuer_keys'])
                        or replace(saved.config,issuer_keys=()).content_hash!=frozen['partition']['config_hash']):
                    raise ValueError('Collection estimate member changed')
                measured=capacity.plan_demands(saved.config,saved.units)
                for name,count in measured['demands'].items():demands[name]=demands.get(name,0)+count
                for name,count in measured['active_units'].items():active[name]=active.get(name,0)+count
                for name,count in measured['unknown_products'].items():unknown[name]=unknown.get(name,0)+count
                members.append((saved.dataset_id,saved.plan_hash))
            demands['collection_metadata']=len(members)+2;active['collection_metadata']=1
            result=dict(status='measurement_required',demands=demands,active_units=active,unknown_products=unknown,
                collection_hash=frozen['collection_hash'],required_bytes=None,storage=capacity.inventory(query,database))
        if store.load()!=frozen:raise ValueError('Collection changed during storage estimation')
    else:
        saved=campaign.load_plan(query,database,dataset_id)
        if profile is not None:
            result=capacity_profile.factory(query,profile)(saved.config,saved.units)(saved.plan_hash,saved.units)
        else:
            demand=capacity.plan_demands(saved.config,saved.units)
            result=dict(status='measurement_required',plan_hash=saved.plan_hash,required_bytes=None,
                **demand,storage=capacity.inventory(query,database))
        if campaign.load_plan(query,database,dataset_id).plan_hash!=saved.plan_hash:
            raise ValueError('Stored plan changed during storage estimation')
    result=dict(result,read_only=True,execution_started=False,
        limitation='Capacity estimate only; source qualification and dataset certification are separate.')
    return dict(result,estimate_hash=digest(result))
