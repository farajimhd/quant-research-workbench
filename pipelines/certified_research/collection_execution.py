"""Stored collection execution through the existing owned child launch paths."""
from types import SimpleNamespace
from . import campaign,collection_store,capacity_profile,saved_execution,verification


def execute(query,database,collection_id,claim,child_claim_factory,client_factory,
            dictionary_claim_factory,*,profile=None,verify_only=False,stop=None,progress=None):
    store=collection_store.Store(query,database,collection_id,claim)
    frozen=store.load()
    if not verify_only and profile is None:raise ValueError('Collection execution requires a qualified capacity profile')
    load=lambda id_:campaign.load_plan(query,database,id_)
    admission=capacity_profile.factory(query,profile) if not verify_only else None
    stop_event=stop
    def launch(id_):
        saved=load(id_)
        def run(stop=None):
            child=child_claim_factory(id_)
            try:
                return saved_execution.run(query,database,id_,child,client_factory,
                    dictionary_claim_factory,admission,stop=stop_event,progress=progress)
            finally:child.close()
        def verify():
            if stop_event is not None and stop_event.is_set():
                raise InterruptedError('Collection verification interrupted')
            child=child_claim_factory(id_)
            try:
                return verification.verify(query,database,id_,child,client_factory,
                    dictionary_claim_factory)['root_hash']
            finally:child.close()
        return SimpleNamespace(controller=SimpleNamespace(dataset=id_,plan_hash=saved.plan_hash),
            config=saved.config,run=run,verify=verify)
    # Child execution/verification rechecks canonical/reporting epochs and all
    # reference authorities; collection guards recheck immutable membership.
    aggregate=(lambda value:capacity_profile.collection_guard(query,database,value,load,profile)(value))
    controller=store.controller(launch,claim.check,aggregate)
    if verify_only:
        certificate=controller.verify()
        seals=store.rows('research_collection_seal_v1')
        if len(seals)!=1 or seals[0]['collection_hash']!=certificate['collection_hash'] or seals[0]['root_hash']!=certificate['root_hash']:
            raise ValueError('Published collection seal is missing or differs from verified authority')
        roots=tuple((str(m[0]),m[1],m[2]) for m in seals[0]['member_roots'])
        if roots!=certificate['members']:raise ValueError('Published collection member roots differ')
        return dict(status='verified',certificate=certificate,read_only=True)
    return controller.run(stop=(lambda:stop.is_set()) if stop is not None else None,
                          progress=(lambda change:progress(dict(phase='collection',counts=change))) if progress else None)
