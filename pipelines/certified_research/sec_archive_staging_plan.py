"""Read-only grouped G→D SEC archive plan with exact reserve-aware chunks."""
from datetime import date
from pathlib import Path,PureWindowsPath
from .contracts import digest
from pipelines.market_sip.flatfiles import archive_staging


def plan(prepared,canonical_root,backup_root,cache_root,runtime_root,*,reserve_bytes=256*1024**3,
         reserve_fraction=.10,maximum_chunk_bytes=256*1024**3,reuse_lookup=None):
    if not isinstance(prepared,(tuple,list)) or not 1<=len(prepared)<=1000:
        raise ValueError('SEC archive plan needs 1 to 1000 prepared documents')
    canonical_root=PureWindowsPath(canonical_root)
    if not canonical_root.is_absolute() or canonical_root.drive.upper()!='D:' or '..' in canonical_root.parts:
        raise ValueError('SEC canonical archive root must be an explicit absolute D path')
    backup_root=Path(backup_root).resolve();cache_root=Path(cache_root).resolve();runtime_root=Path(runtime_root).resolve()
    if not runtime_root.is_dir():raise ValueError('Required runtime root is unavailable')
    if cache_root==runtime_root or not cache_root.is_relative_to(runtime_root):
        raise ValueError('SEC private staging must stay below the operational runtime root')
    archive_staging.validate_roots(backup_root,cache_root)
    groups={};seen=set()
    for source in prepared:
        body=dict(source);hash_=body.pop('prepared_hash',None)
        if hash_!=digest(body):raise ValueError('SEC prepared source fingerprint differs')
        key=source['document']['document_key']
        if key in seen:raise ValueError('SEC staging documents are duplicated')
        seen.add(key);document=source['document']['lineage']['document']
        path=PureWindowsPath(document['source_archive_path'])
        if '..' in path.parts:raise ValueError('SEC archive path contains traversal')
        try:relative=path.relative_to(canonical_root)
        except ValueError as error:raise ValueError('SEC archive escapes its declared canonical root') from error
        day=date.fromisoformat(document['source_archive_date'])
        expected=PureWindowsPath(str(day.year),f'QTR{(day.month-1)//3+1}',day.strftime('%Y%m%d')+'.nc.tar.gz')
        if relative!=expected:raise ValueError('SEC archive date/quarter/file identity differs')
        name=expected.as_posix()
        groups.setdefault(name,[]).append(dict(document_key=key,prepared_hash=hash_))
    archives=[]
    for name,documents in sorted(groups.items()):
        source=(backup_root/Path(name)).resolve();target=(cache_root/Path(name)).resolve()
        if not source.is_relative_to(backup_root) or not target.is_relative_to(cache_root):
            raise ValueError('SEC archive path escapes its source/staging roots')
        if not source.is_file():raise ValueError('SEC retained backup archive is missing: '+name)
        stat=source.stat()
        if stat.st_size<=0:raise ValueError('SEC retained backup archive is empty')
        archives.append(dict(archive_key=name,source=str(source),destination=str(target),bytes=stat.st_size,
            source_mtime_ns=stat.st_mtime_ns,documents=tuple(sorted(documents,key=lambda d:d['document_key']))))
    space=archive_staging.space_budget(cache_root,reserve_bytes=reserve_bytes,reserve_fraction=reserve_fraction)
    reused=tuple(reuse_lookup(tuple(archives))) if reuse_lookup is not None else ()
    if len(set(reused))!=len(reused) or set(reused)-{row['archive_key'] for row in archives}:
        raise ValueError('SEC pre-plan reuse census differs')
    cached=tuple((row['archive_key'],row['bytes']) for row in archives
        if row['archive_key'] in reused or (Path(row['destination']).is_file() and Path(row['destination']).stat().st_size==row['bytes']))
    cached_keys={key for key,size in cached}
    chunks=archive_staging.plan_chunks(((row['archive_key'],row['bytes']) for row in archives if row['archive_key'] not in cached_keys),space,
        maximum_chunk_bytes=maximum_chunk_bytes)
    chunks=dict(chunks,copy_bytes=chunks['total_bytes'],total_bytes=sum(row['bytes'] for row in archives),
        chunks=((cached,) if cached else ())+chunks['chunks'])
    result=dict(version='grouped-sec-archive-staging-plan-v2',archives=tuple(archives),chunks=chunks,
        cached_unverified=tuple(sorted(cached_keys-set(reused))),publication_reuse=tuple(sorted(reused)),
        documents=len(seen),unique_archives=len(archives),space=space,copy_started=False,
        backup_root=str(backup_root),cache_root=str(cache_root),runtime_root=str(runtime_root))
    return dict(result,plan_hash=digest(result))
