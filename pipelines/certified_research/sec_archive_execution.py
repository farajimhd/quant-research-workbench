"""Reserve-aware grouped SEC copies, verification and publication handoff."""
from hashlib import sha256
from pathlib import Path
import re

from pipelines.market_sip.flatfiles import archive_staging
from . import sec_archive_witness,sec_public_archive_time
from .contracts import digest


def execute(plan,prepared,receipts,claim,stop,publish,source_guard,*,mode,
            reserve_bytes=256*1024**3,reserve_fraction=.10,maximum_chunk_bytes=256*1024**3,progress=None,reuse=None):
    """Copy each group once; delete only new owned files after durable readback.

    The publication callback must persist and independently verify all witnesses
    and time qualifications. No metadata files or local restart ledger are made.
    Existing cached files are hash-verified and preserved, never assumed owned.
    """
    body=dict(plan);hash_=body.pop('plan_hash',None)
    if hash_!=digest(body) or plan.get('copy_started') is not False or not callable(publish):
        raise ValueError('Invalid frozen SEC archive plan/publication handoff')
    if mode not in ('acceptance_checked_day','official_filing_day'):raise ValueError('Explicit SEC timing mode is required')
    runtime=Path(plan['runtime_root']).resolve();cache=Path(plan['cache_root']).resolve();backup=Path(plan['backup_root']).resolve()
    if not runtime.is_dir() or cache==runtime or not cache.is_relative_to(runtime):
        raise ValueError('Required SEC staging runtime scope is unavailable')
    archive_staging.validate_roots(backup,cache)
    if not isinstance(prepared,(tuple,list)) or len(prepared)!=plan['documents']:
        raise ValueError('SEC execution source census differs')
    sources={s['document']['document_key']:s for s in prepared}
    if len(sources)!=len(prepared):raise ValueError('SEC execution documents are duplicated')
    for source in prepared:
        body=dict(source);expected_hash=body.pop('prepared_hash',None)
        if expected_hash!=digest(body):raise ValueError('SEC execution prepared fingerprint differs')
    archives={a['archive_key']:a for a in plan['archives']}
    if len(archives)!=len(plan['archives']):raise ValueError('SEC execution archives are duplicated')
    planned=[key for chunk in plan['chunks']['chunks'] for key,size in chunk]
    if len(planned)!=len(set(planned)) or set(planned)!=set(archives):raise ValueError('SEC chunk census differs')
    selected=[]
    for group in archives.values():
        for document in group['documents']:
            source=sources.get(document['document_key'])
            if source is None or source['prepared_hash']!=document['prepared_hash']:
                raise ValueError('SEC execution prepared source differs')
            selected.append(document['document_key'])
    if len(selected)!=len(set(selected)) or set(selected)!=set(sources):raise ValueError('SEC archive/document relationship differs')
    def guard():
        claim.check();source_guard();claim.check()
        if stop.is_set():raise InterruptedError('SEC archive execution stopped; staged evidence retained')
    def emit(phase,group):
        if progress:progress(dict(phase=phase,archive=group['archive_key']))
    completed=[];preserved=[];reused=[]
    for chunk in plan['chunks']['chunks']:
        guard();space=archive_staging.space_budget(cache,reserve_bytes=reserve_bytes,reserve_fraction=reserve_fraction)
        archive_staging.plan_chunks(chunk,space,maximum_chunk_bytes=maximum_chunk_bytes)
        for name,size in chunk:
            guard();group=archives[name];source=Path(group['source']).resolve();target=Path(group['destination']).resolve()
            if (size!=group['bytes'] or not source.is_relative_to(backup) or not target.is_relative_to(cache)):
                raise ValueError('SEC chunk file/path scope differs')
            stat=source.stat()
            if (stat.st_size,stat.st_mtime_ns)!=(size,group['source_mtime_ns']):raise ValueError('SEC backup changed after planning')
            receipt=receipts.get(name)
            if (receipt is None or receipt.get('status')!='downloaded' or receipt.get('byte_size')!=size
                    or not re.fullmatch('[0-9a-f]{64}',receipt.get('sha256') or '')):
                raise ValueError('Original successful SEC download receipt is missing or incompatible')
            documents=tuple(sources[d['document_key']] for d in group['documents'])
            if reuse is not None:
                guard();retained=reuse(documents,receipt,mode=mode);guard()
                if retained is not None:
                    if (retained.get('persisted') is not True or retained.get('readback_verified') is not True
                            or len(retained.get('witness_hashes',()))!=len(documents)
                            or len(retained.get('qualification_hashes',()))!=len(documents)):
                        raise ValueError('SEC retained group readback is incomplete')
                    reused.append(name);completed.append(name);emit('reused',group)
                    continue
            emit('copy',group)
            copied=archive_staging.copy_file(source,target,backup,cache,size,stop=stop,
                reserve_bytes=reserve_bytes,reserve_fraction=reserve_fraction)
            owned=copied['status']=='staged'
            if copied['status']=='cached':
                checksum=sha256()
                with target.open('rb') as reader:
                    while data:=reader.read(8*1024**2):guard();checksum.update(data)
                copied=dict(status='staged',bytes=size,sha256=checksum.hexdigest())
            elif not owned:raise ValueError('SEC backup could not be staged')
            if copied['sha256']!=receipt['sha256']:raise ValueError('SEC staged archive differs from original download digest')
            guard();emit('verify',group)
            witnesses=sec_archive_witness.inspect_archives(target,documents,stop=stop,staging_receipt=copied)
            qualifications=tuple(sec_public_archive_time.qualify(w,receipt,mode=mode) for w in witnesses)
            guard();emit('publish',group)
            result=publish(witnesses,qualifications)
            expected=dict(witness_hashes=tuple(w['witness_hash'] for w in witnesses),
                qualification_hashes=tuple(q['qualification_hash'] for q in qualifications))
            if (not isinstance(result,dict) or result.get('persisted') is not True or result.get('readback_verified') is not True
                    or any(tuple(result.get(k,()))!=v for k,v in expected.items())):
                raise ValueError('SEC witness publication/readback did not verify the complete group')
            guard()
            if owned:
                if not target.resolve().is_relative_to(cache):raise ValueError('SEC cleanup escapes private staging')
                target.unlink()
            else:preserved.append(str(target))
            completed.append(name);emit('completed',group)
    return dict(status='complete',completed=tuple(completed),preserved_cached_copies=tuple(preserved),
        reused=tuple(reused),public_time_mode=mode,allocation_certified=False)


def publish_retained(plan,prepared,receipts,query,database,claim,stop,source_guard,admission,*,mode,**options):
    """Concrete archive-to-ClickHouse path under caller-owned source/admission guards."""
    from .sec_witness_publication import Publisher
    publisher=Publisher(query,database,claim,source_guard,admission)
    by_digest={}
    for receipt in receipts.values():
        key=receipt.get('sha256')
        if key in by_digest and by_digest[key]!=receipt:
            raise ValueError('Competing original download receipts for one archive digest')
        by_digest[key]=receipt
    def publish(witnesses,qualifications):
        receipt=by_digest.get(witnesses[0]['container_sha256'])
        if receipt is None:raise ValueError('Original witness receipt is unavailable')
        return publisher.publish(witnesses,qualifications,receipt)
    return execute(plan,prepared,receipts,claim,stop,publish,source_guard,mode=mode,reuse=publisher.reuse,**options)
