"""Freeze complete issuer collections one bounded child graph at a time."""
from dataclasses import replace
from datetime import date
import json
import re
from .contracts import digest
from . import campaign,cohorts,collection,collection_store,context,fresh_execution,initial_plan,source,universe
from . import verification


def verify_child_inputs(query,saved):
    """Preparation reuse proves current inputs, not merely matching config."""
    verification.source_guard(query,saved)()
    units=[p for p in saved.units if p.unit.product=='universe']
    if len(units)!=1:raise ValueError('Prepared child requires one frozen universe authority')
    args=units[0].arguments
    if args.get('end')!=str(saved.config.end):raise ValueError('Prepared child universe target end changed')
    if tuple(args.get('issuer_keys',()))!=saved.config.issuer_keys:
        raise ValueError('Prepared child universe selection changed')
    if args.get('conid_review_hash')!=saved.config.conid_review_hash:
        raise ValueError('Prepared child conid review selection changed')
    snapshot=cohorts.select(universe.load_for_config(query,date.fromisoformat(args['context_start']),
        date.fromisoformat(args['end']),date.fromisoformat(args['anchor_date']),saved.config),saved.config.issuer_keys)
    if not snapshot['ready'] or snapshot['source_hash']!=args['source_hash']:
        raise ValueError('Prepared child universe input changed')


def prepare(query,config,collection_id,claim,child_claim_factory,client_factory,
            dictionary_claim_factory,capacity_factory,*,context_start,fundamental_start,anchor_date,
            maximum_issuers=128,maximum_sessions=200000,stop=None,progress=None,rebuild_origin=None,source_preparer=None,sec_scope_preparer=None,sec_scope_preparation_hash=None):
    if sec_scope_preparer is not None and (not isinstance(sec_scope_preparation_hash,str) or not re.fullmatch('[0-9a-f]{64}',sec_scope_preparation_hash)):
        raise ValueError('Collection SEC scope preparation requires its frozen selection hash')
    if sec_scope_preparation_hash is not None and sec_scope_preparer is None:
        raise ValueError('Collection SEC scope selection has no preparation provider')
    if config.issuer_keys:raise ValueError('Fresh collection requires the complete unscoped config')
    claim.check()
    targets=source.exchange_sessions(config.start,config.end)
    if not targets:raise ValueError('Fresh collection has no target exchange sessions')
    short_days=context.preceding_sessions(targets[0],max(config.short_windows_sessions))
    start=min((context_start,*short_days,*targets))
    days=source.exchange_sessions(start,config.end)
    snapshot=universe.load_for_config(query,start,config.end,anchor_date,config)
    if not snapshot['ready']:
        return dict(status='blocked',phase='historical-universe',rejections=snapshot['rejection_counts'],
                    history_dependencies=universe.history_repair_inventory(query,snapshot),execution_started=False)
    partition=cohorts.partition_snapshot(snapshot,config,days,maximum_issuers=maximum_issuers,maximum_sessions=maximum_sessions)
    # These launch inputs are not in CampaignConfig. They must still change
    # member identities, or an interrupted launch could reuse narrower history.
    partition.pop('partition_hash')
    partition['preparation_inputs']=dict(context_start=str(context_start),fundamental_start=str(fundamental_start),
        anchor_date=str(anchor_date),rebuild_origin=str(rebuild_origin) if rebuild_origin is not None else None)
    if sec_scope_preparation_hash is not None:partition['preparation_inputs']['sec_scope_preparation_hash']=sec_scope_preparation_hash
    partition['partition_hash']=digest(partition)
    members=[]
    for index,group in enumerate(partition['groups']):
        claim.check()
        if stop is not None and stop.is_set():raise InterruptedError('Collection preparation interrupted')
        if universe.load_for_config(query,start,config.end,anchor_date,config)['source_hash']!=partition['full_source_hash']:
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
                    stop=stop,progress=progress,rebuild_origin=rebuild_origin,source_preparer=source_preparer)
                if not assembled.get('ready'):
                    return dict(status='blocked',member=id_,completed=index,total=len(partition['groups']),
                                missing_authorities=assembled.get('missing_authorities',()))
                launch=initial_plan.build_launch(query,child_config,id_,assembled,child,client_factory,
                    dictionary_claim_factory,capacity_factory,stop=stop,progress=progress,
                    **({'sec_scope_preparer':sec_scope_preparer} if sec_scope_preparer is not None else {}))
                # Campaign.install compares the complete header and every extant
                # row before filling missing metadata. Drift cannot overwrite it.
                launch.admit();launch.controller.install()
                saved=campaign.load_plan(query,config.database,id_)
            if saved.config!=child_config:raise ValueError('Prepared collection child config changed')
            verify_child_inputs(query,saved)
            members.append(collection.Member(id_,tuple(group),saved.plan_hash))
        finally:child.close()
    claim.check()
    if universe.load_for_config(query,start,config.end,anchor_date,config)['source_hash']!=partition['full_source_hash']:
        raise ValueError('Full universe changed before collection install')
    for member in members:
        claim.check()
        verify_child_inputs(query,campaign.load_plan(query,config.database,member.dataset_id))
    frozen=collection.definition(collection_id,partition,members)
    collection_store.Store(query,config.database,collection_id,claim).install(frozen)
    return dict(status='prepared',collection_hash=frozen['collection_hash'],members=len(members),execution_started=False)
