"""Freeze complete issuer collections one bounded child graph at a time."""
from dataclasses import replace
import json
from .contracts import digest
from . import campaign,cohorts,collection,collection_store,context,fresh_execution,initial_plan,source,universe


def prepare(query,config,collection_id,claim,child_claim_factory,client_factory,
            dictionary_claim_factory,capacity_factory,*,context_start,fundamental_start,anchor_date,
            maximum_issuers=128,maximum_sessions=200000,stop=None,progress=None,rebuild_origin=None):
    if config.issuer_keys:raise ValueError('Fresh collection requires the complete unscoped config')
    claim.check()
    targets=source.exchange_sessions(config.start,config.end)
    if not targets:raise ValueError('Fresh collection has no target exchange sessions')
    short_days=context.preceding_sessions(targets[0],max(config.short_windows_sessions))
    start=min((context_start,*short_days,*targets))
    days=source.exchange_sessions(start,config.end)
    snapshot=universe.load(query,start,config.end,anchor_date)
    if not snapshot['ready']:
        return dict(status='blocked',phase='historical-universe',rejections=snapshot['rejection_counts'],
                    history_dependencies=universe.history_repair_inventory(query,snapshot),execution_started=False)
    partition=cohorts.partition_snapshot(snapshot,config,days,maximum_issuers=maximum_issuers,maximum_sessions=maximum_sessions)
    members=[]
    for index,group in enumerate(partition['groups']):
        claim.check()
        if stop is not None and stop.is_set():raise InterruptedError('Collection preparation interrupted')
        if universe.load(query,start,config.end,anchor_date)['source_hash']!=partition['full_source_hash']:
            raise ValueError('Full universe changed during collection preparation')
        id_=collection.member_id(collection_id,partition['partition_hash'],index)
        child_config=replace(config,issuer_keys=tuple(group));child=child_claim_factory(id_)
        try:
            existing=query(f'SELECT count() n FROM {config.database}.research_dataset_v1 '
                           f"WHERE dataset_id=toUUID('{id_}')")[0]['n']
            pending=False
            if int(existing):
                headers=query(f'SELECT definition,contract_hash FROM {config.database}.research_dataset_v1 '
                              f"WHERE dataset_id=toUUID('{id_}')")
                if len(headers)!=1 or len(headers[0]['definition'].encode())>256*1024**2:
                    raise ValueError('Partial child header is duplicated or oversized')
                definition=json.loads(headers[0]['definition'])
                count=definition.get('unit_count')
                if digest(definition)!=headers[0]['contract_hash'] or type(count) is not int or not 1<=count<=1000000:
                    raise ValueError('Partial child header fingerprint/count is invalid')
                census=query(f'SELECT count() n,uniqExact(unit_id) ids FROM {config.database}.research_campaign_unit_v1 '
                             f"WHERE dataset_id=toUUID('{id_}')")[0]
                if int(census['n'])!=int(census['ids']) or int(census['n'])>count:
                    raise ValueError('Partial child graph contains duplicate or extra units')
                pending=int(census['n'])<count
            if int(existing) and not pending:
                saved=campaign.load_plan(query,config.database,id_)
                if saved.config!=child_config:raise ValueError('Prepared collection child config changed')
            else:
                assembled=fresh_execution.prepare(query,child_config,id_,child,client_factory,
                    dictionary_claim_factory,capacity_factory,context_start=context_start,
                    fundamental_start=fundamental_start,anchor_date=anchor_date,
                    stop=stop,progress=progress,rebuild_origin=rebuild_origin)
                if not assembled.get('ready'):
                    return dict(status='blocked',member=id_,completed=index,total=len(partition['groups']),
                                missing_authorities=assembled.get('missing_authorities',()))
                launch=initial_plan.build_launch(query,child_config,id_,assembled,child,client_factory,
                    dictionary_claim_factory,capacity_factory,stop=stop,progress=progress)
                # Campaign.install compares the complete header and every extant
                # row before filling missing metadata. Drift cannot overwrite it.
                launch.admit();launch.controller.install()
                saved=campaign.load_plan(query,config.database,id_)
            members.append(collection.Member(id_,tuple(group),saved.plan_hash))
        finally:child.close()
    claim.check()
    if universe.load(query,start,config.end,anchor_date)['source_hash']!=partition['full_source_hash']:
        raise ValueError('Full universe changed before collection install')
    frozen=collection.definition(collection_id,partition,members)
    collection_store.Store(query,config.database,collection_id,claim).install(frozen)
    return dict(status='prepared',collection_hash=frozen['collection_hash'],members=len(members),execution_started=False)
