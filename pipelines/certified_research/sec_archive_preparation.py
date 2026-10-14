"""Unified retained SEC archive stage; metadata stays in bounded memory."""
import json
import sys
from pathlib import Path

from . import sec_archive_execution,sec_archive_staging_plan,sec_archive_witness,sec_public_archive_time
from . import sec_witness_publication,sec_share_source,sec_share_preparation,sec_document_lineage
from .contracts import digest

PRODUCT='sec_archive_witness'


def recipe_hash():
    modules=(sys.modules[__name__],sec_archive_execution,sec_archive_staging_plan,sec_archive_witness,sec_public_archive_time,
             sec_witness_publication,sec_share_source,sec_share_preparation,sec_document_lineage)
    return digest(dict(version='sec-archive-source-execution-v1',
        algorithms={m.__name__:digest(Path(m.__file__).read_text(encoding='utf-8')) for m in modules},
        ddl=tuple(t.ddl('q_live') for t in sec_witness_publication.tables())))


def receipts(path,archives):
    """Stream a bounded original downloader ledger; only selected successes retained."""
    path=Path(path)
    if not path.is_file() or path.stat().st_size>256*1024**2:
        raise ValueError('Original SEC download ledger is missing or exceeds 256MiB')
    wanted={a['archive_key'] for a in archives};selected={}
    before=path.stat()
    with path.open('r',encoding='utf-8') as reader:
        while line:=reader.readline(1024*1024+1):
            if len(line.encode('utf-8'))>1024*1024:raise ValueError('SEC receipt line exceeds 1MiB')
            if not line.strip():continue
            row=json.loads(line)
            if row.get('status')!='downloaded' or row.get('source_kind')!='daily_feed_archive':continue
            key='/'.join(str(row.get('source_url','')).split('/')[-3:])
            if key not in wanted:continue
            source={k:row.get(k) for k in ('source_kind','source_url','source_date','downloaded_at_utc',
                'byte_size','sha256','last_modified','status')}
            if key in selected and selected[key]!=source:raise ValueError('Competing original SEC download receipts')
            selected[key]=source
    after=path.stat()
    if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):
        raise ValueError('Original SEC receipt ledger changed while reading')
    if set(selected)!=wanted:raise ValueError('Original successful SEC receipts are missing for selected archives')
    return selected


def prepare(query,config,requests,claim,dictionary_factory,profile,*,canonical_root,backup_root,cache_root,
            runtime_root,receipt_ledger,mode,stop,reserve_bytes,reserve_fraction,maximum_chunk_bytes,progress=None):
    if not isinstance(requests,(tuple,list)) or not 1<=len(requests)<=1000 or len(set(requests))!=len(requests):
        raise ValueError('SEC archive preparation requires up to 1000 unique requested documents')
    if config.global_memory_bytes<sec_share_preparation.MEMORY+config.query_memory_bytes:
        raise ValueError('SEC archive source parsing requires 2GiB plus query memory')
    def guard():
        claim.check()
        if stop.is_set():raise InterruptedError('SEC archive preparation stopped')
    # Require measured archive-specific capacity before fetching payloads or writing.
    admit=sec_share_preparation.admission(query,profile,len(requests),1,product=PRODUCT,recipe=recipe_hash())
    guard();admit(0);sources=[];metadata_bytes=0
    for request in requests:
        guard();source=sec_share_source.prepare(query,*request,claim,guard)
        descriptor=dict(document=source['document']);descriptor['prepared_hash']=digest(descriptor)
        sources.append(descriptor)
        del source
        metadata_bytes+=len(json.dumps(descriptor).encode('utf-8'))
        if metadata_bytes>64*1024**2:
            raise ValueError('SEC archive source metadata exceeds 64MiB')
    plan=sec_archive_staging_plan.plan(tuple(sources),canonical_root,backup_root,cache_root,runtime_root,
        reserve_bytes=reserve_bytes,reserve_fraction=reserve_fraction,maximum_chunk_bytes=maximum_chunk_bytes)
    original=receipts(receipt_ledger,plan['archives'])
    def source_guard():
        guard()
        for request,source in zip(requests,sources):
            if sec_document_lineage.inspect(query,*request,claim,guard)!=source['document']['lineage']:
                raise ValueError('SEC archive current source lineage changed')
        guard()
    dictionary=dictionary_factory()
    try:sec_witness_publication.install(query,'q_live',dictionary)
    finally:dictionary.close()
    return sec_archive_execution.publish_retained(plan,tuple(sources),original,query,'q_live',claim,stop,source_guard,admit,
        mode=mode,reserve_bytes=reserve_bytes,reserve_fraction=reserve_fraction,
        maximum_chunk_bytes=maximum_chunk_bytes,progress=progress)
