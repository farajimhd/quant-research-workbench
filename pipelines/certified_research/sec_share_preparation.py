"""Unified retained SEC candidate stage with measured source-write admission."""
from pathlib import Path
from threading import Event
import re
import sys

from . import capacity,sec_share_capture,sec_share_source,sec_share_context,sec_document_lineage
from .contracts import digest
from .execution import Unit,execute

PRODUCT='sec_share_source'
MEMORY=2*1024**3


def request(value):
    fields=value.split(',') if isinstance(value,str) else ()
    if (len(fields)!=3 or not re.fullmatch('0*[1-9][0-9]{0,9}',fields[0])
            or not re.fullmatch('[0-9]{10}-[0-9]{2}-[0-9]{6}',fields[1]) or not 1<=len(fields[2])<=256):
        raise ValueError('Use CIK,ACCESSION,DOCUMENT_NAME')
    return (str(int(fields[0])),fields[1],fields[2])


def recipe_hash():
    from pipelines.sec.edgar.sec_pipeline import revision
    modules=(sys.modules[__name__],sec_share_capture,sec_share_source,sec_share_context,sec_document_lineage,revision)
    return digest(dict(version='sec-share-source-execution-v1',python=tuple(sys.version_info[:2]),
        algorithms={m.__name__:digest(Path(m.__file__).read_text(encoding='utf-8')) for m in modules},
        ddl=tuple(t.ddl('q_live') for t in sec_share_capture.tables()),memory_reservation=MEMORY,
        maximum_source_bytes=16*1024**2,maximum_facts=10000,maximum_metadata_bytes=64*1024**2))


def admission(query,profile,documents,active):
    # Recheck the loaded profile, including its parsed objects, on every call.
    def admit(metadata_bytes):
        if profile['profile_hash']!=digest({k:v for k,v in profile.items() if k not in ('profile_hash','parsed_rates')}):
            raise ValueError('SEC source capacity profile changed')
        rates=tuple(capacity.PilotRate(**row) for row in profile['rates'])
        if rates!=profile['parsed_rates']:raise ValueError('SEC source parsed capacity rates differ')
        rate=next((r for r in rates if r.product==PRODUCT),None)
        if rate is None or rate.policy!='live_market_ssd':raise ValueError('SEC candidates require a qualified SSD sec_share_source pilot rate')
        result=capacity.admission(capacity.inventory(query,'q_live'),{PRODUCT:documents},rates,
            {PRODUCT:recipe_hash()},active_units={PRODUCT:active},reserve_bytes=profile['reserve_bytes'],
            volume_groups=profile['volume_groups'],safety_factor=profile['safety_factor'])
        if result['status']!='admitted':raise ValueError('SEC source capacity admission blocked: '+str(result.get('blockers',())))
        body=dict(result,metadata_bytes=metadata_bytes);body.pop('certificate_hash',None)
        return dict(body,certificate_hash=digest(body))
    return admit


def prepare(query,config,requests,claim,client_factory,dictionary_factory,profile,*,stop=None,progress=None):
    if not isinstance(requests,(tuple,list)) or not 1<=len(requests)<=1000:
        raise ValueError('SEC source preparation needs 1 to 1000 explicit documents')
    requests=tuple(request(','.join(r)) for r in requests)
    if len(set(requests))!=len(requests):raise ValueError('SEC source documents are duplicated')
    reservation=MEMORY+config.query_memory_bytes
    if config.global_memory_bytes<reservation:raise ValueError('SEC source parsing needs 2GiB plus its query-memory reservation')
    workers=min(len(requests),config.workers,config.global_memory_bytes//reservation)
    stop=Event() if stop is None else stop
    def guard():
        claim.check()
        if stop.is_set():raise InterruptedError('SEC candidate source preparation stopped')
    guard()
    # Missing/incompatible pilot evidence blocks before schema installation.
    admission(query,profile,len(requests),workers)(0)
    dictionary=dictionary_factory()
    try:
        if dictionary.dictionary_database!='q_live':raise ValueError('SEC source installation requires q_live ownership')
        sec_share_capture.install(query,'q_live',dictionary)
    finally:dictionary.close()
    jobs=tuple(Unit(str(index),PRODUCT,memory_bytes=reservation) for index in range(len(requests)))
    def run(unit,parents,signal):
        guard();client=client_factory()
        try:
            source=sec_share_source.prepare(client.query,*requests[int(unit.key)],claim,guard)
            result=sec_share_capture.capture(client.query,'q_live',source,claim,guard,
                admission=admission(client.query,profile,len(requests),workers))
            guard();return dict(result,output_hash=result['prepared_hash'])
        finally:client.close()
    def report(event):
        if progress is None:return
        counts=dict(event['counts']);counts['prepared']=counts.pop('certified',0)+counts.pop('reused',0)
        status='prepared' if event['status'] in ('certified','reused') else event['status']
        progress(dict(event,phase='sec-share-source-candidates',counts=counts,status=status))
    result=execute(jobs,{PRODUCT:run},workers=workers,memory_bytes=config.global_memory_bytes,stop=stop,progress=report)
    return dict(result,candidate_only=True,original_vintage_qualified=False,allocation_certified=False,
        captured=sum(r['inserted_rows']>0 for r in result['results'].values()),
        reused=sum(r['inserted_rows']==0 for r in result['results'].values()))
