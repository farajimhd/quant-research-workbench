"""Opt-in runtime and query defaults for the unified SEC acquisition chain."""
from dataclasses import replace
from pathlib import Path,PurePosixPath
import hashlib,json
from uuid import uuid4
from research.mlops.clickhouse import sql_string


def configure(args):
    root=getattr(args,'runtime_root_win',None)
    names=('execution_workers','execution_query_threads','execution_query_memory_bytes','execution_global_memory_bytes')
    if root is None:
        if getattr(args,'runtime_root_ch',None) is not None or any(getattr(args,name,None) is not None for name in names):
            raise ValueError('Scoped SEC resource settings require --runtime-root-win')
        return None
    root=Path(root).resolve()
    bases=(Path('D:/TradingML/runtimes'),Path(r'\\DESKTOP-SAAI85T\Workstation-D\TradingML\runtimes'))
    base=next((base for base in bases if root==base or base in root.parents),None)
    if base is None or not base.is_dir():raise ValueError('Required SEC runtime root is unavailable or outside the operational runtime tree')
    workers=args.execution_workers if args.execution_workers is not None else 4
    threads=args.execution_query_threads if args.execution_query_threads is not None else 2
    memory=args.execution_query_memory_bytes if args.execution_query_memory_bytes is not None else 2*1024**3
    global_memory=args.execution_global_memory_bytes if args.execution_global_memory_bytes is not None else 16*1024**3
    if (not 1<=workers<=8 or not 1<=threads<=8 or not 64*1024**2<=memory<=32*1024**3
            or not memory*workers<=global_memory<=256*1024**3):
        raise ValueError('Invalid bounded SEC worker/query memory configuration')
    for name in vars(args):
        if name.endswith('output_root_win'):setattr(args,name,str(root/name.removesuffix('_root_win')))
    relative=root.relative_to(base).as_posix()
    mount='/mnt/d/TradingML/runtimes'+('/'+relative if relative!='.' else '')
    if getattr(args,'runtime_root_ch',None) is not None:mount=args.runtime_root_ch
    if not mount.startswith('/mnt/d/') or '..' in PurePosixPath(mount).parts or '\\' in mount:
        raise ValueError('SEC runtime ClickHouse mapping must be an absolute D SSD path without traversal')
    args.artifact_root_win=str(root/'source');args.parts_root_win=str(root/'parts')
    args.bulk_file_root_ch=mount+'/source';args.parts_root_ch=mount+'/parts'
    for name in ('bulk_download_concurrency','archive_download_concurrency','archive_validation_workers',
            'text_extract_workers','entity_backfill_workers','xbrl_workers','text_insert_concurrency'):
        setattr(args,name,workers)
    for name in ('bulk_ingest_max_threads','text_ingest_max_threads','text_worker_insert_max_threads'):
        setattr(args,name,threads)
    for name in ('bulk_ingest_max_memory','text_ingest_max_memory_usage','text_worker_insert_max_memory_usage'):
        setattr(args,name,str(memory))
    return dict(runtime_root=str(root),runtime_root_ch=mount,workers=workers,query_threads=threads,
        query_memory_bytes=memory,global_memory_bytes=global_memory,
        storage_policy='live_market_ssd')


def preflight_runtime_access(profile,client):
    """Prove the configured host directory is readable through the CH alias."""
    records=[json.loads(line) for line in client.execute("SELECT value FROM system.server_settings WHERE name='user_files_path' FORMAT JSONEachRow").splitlines() if line.strip()]
    if len(records)!=1:raise ValueError('Cannot verify ClickHouse SEC runtime file authority')
    allowed=PurePosixPath(records[0]['value']);mapped=PurePosixPath(profile['runtime_root_ch'])
    if mapped!=allowed and allowed not in mapped.parents:
        raise ValueError('SEC runtime mapping is outside ClickHouse user_files_path; configure a verified runtime mount alias before acquisition')
    root=Path(profile['runtime_root']);root.mkdir(parents=True,exist_ok=True)
    nonce=uuid4().hex.encode('ascii');name='sec-runtime-probe-'+uuid4().hex+'.bin';path=root/name
    expected=hashlib.sha256(nonce).hexdigest()
    created=False
    try:
        with path.open('xb') as output:
            created=True;output.write(nonce)
        sql=f"SELECT lower(hex(SHA256(payload))) proof FROM file({sql_string(str(mapped/name))},'RawBLOB','payload String') FORMAT JSONEachRow"
        result=[json.loads(line) for line in client.execute(sql).splitlines() if line.strip()]
        if result!=[dict(proof=expected)]:raise ValueError('SEC runtime mount does not read back the host file exactly')
    finally:
        if created:path.unlink()
    return dict(runtime_root_ch=str(mapped),user_files_path=str(allowed),proof_hash=expected)


def environment(profile):
    return {} if profile is None else dict(SEC_EXECUTION_MAX_THREADS=str(profile['query_threads']),
        SEC_EXECUTION_MAX_MEMORY_BYTES=str(profile['query_memory_bytes']),
        SEC_CLICKHOUSE_STORAGE_POLICY='live_market_ssd',CLICKHOUSE_LIVE_STORAGE_POLICY='live_market_ssd',
        PYTHONDONTWRITEBYTECODE='1')


def commands(args,plans):
    """Override only known child options; keep the original stage graph."""
    profile=getattr(args,'execution_profile',None)
    if profile is None:return plans
    threads=str(profile['query_threads']);memory=str(profile['query_memory_bytes'])
    overrides={
        'bulk-canonicalize':{'--output-root-win':str(Path(args.core_output_root_win)/'canonical')},
        'acceptance-raw-metadata-repair':{'--max-threads':threads},
        'missing-document-repair':{'--insert-max-threads':threads,'--insert-max-memory-usage':memory},
        'archive-identity-repair':{'--insert-max-threads':threads,'--insert-max-memory-usage':memory},
        'sec-context-build':{'--max-threads':threads,'--max-memory-usage':memory},
    }
    storage={'bulk-ingest','archive-text-rebuild','acceptance-submissions-enrichment',
        'sec-issuer-relationship-sync','sec-context-build'}
    result=[]
    for plan in plans:
        command=list(plan.command);settings=dict(overrides.get(plan.stage,{}))
        if plan.stage in storage:settings['--storage-policy']='live_market_ssd'
        for flag,value in settings.items():
            if flag in command:command[command.index(flag)+1]=value
            else:command.extend((flag,value))
        result.append(replace(plan,command=command))
    return result
