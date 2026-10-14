"""Authorized ingestion-only recovery from G backups, staged and read on D.

Consumers still read canonical ClickHouse events. This migration extracts clocks
and full conditions, reconciles original trade fields, then repairs only flags.
It never downloads, replaces prices/ordinals, or deletes backup/cache originals.
"""
from datetime import date,datetime,timezone
from pathlib import Path

from . import archive_staging,download_update_events as updater
from .download_massive_sip_flatfiles import DownloadJob
from pipelines.market_sip.events import reporting_evidence as ns,native_reporting_repair as native
from pipelines.certified_research import schema
from pipelines.certified_research import source as canonical_source
from pipelines.certified_research.contracts import digest,literal
from pipelines.certified_research.publication import insert_rows,SourceClaim

RECEIPTS='historical_retained_reporting_capture_v1'
STAGE=ns.TABLE+'_retained_stage'
RAW=ns.TABLE+'_retained_raw'
CANONICAL=ns.TABLE+'_retained_canonical'


def receipt_table():
    return schema.Table(RECEIPTS,(schema.c('source_date','Date'),schema.c('source_digest','FixedString(64)'),
        schema.c('evidence_hash','FixedString(64)'),schema.c('file_digest','FixedString(64)'),
        schema.c('file_bytes','UInt64','T64,ZSTD(1)'),schema.c('file_key','LowCardinality(String)'),
        schema.c('source_origin','UInt8'),schema.c('created_at',"DateTime64(6,'UTC')",'Delta,ZSTD(1)')),
        ('source_date','source_digest'),'toYYYYMM(source_date)')


def raw_table():
    """Temporary ingestion projection preserves canonical widths and raw clocks."""
    columns=[schema.c('source_date','Date'),schema.c('ticker','LowCardinality(String)'),
        schema.c('event_meta','UInt8'),schema.c('sip_timestamp_us','UInt64','Delta,ZSTD(1)'),
        schema.c('sequence_number','UInt32','Delta,ZSTD(1)')]
    columns.extend(schema.c(n,'UInt32','T64,ZSTD(1)') for n in ('price_primary_int','price_secondary_int'))
    columns.extend(schema.c(n,'Float32','Gorilla,ZSTD(1)') for n in ('size_primary','size_secondary'))
    columns.extend(schema.c(n,'UInt8') for n in ('exchange_primary','exchange_secondary',*(f'condition_token_{i}' for i in range(1,6))))
    columns.extend((schema.c('event_date','Date'),schema.c('sip_timestamp_ns','UInt64','Delta,ZSTD(1)'),
        schema.c('participant_timestamp_ns','Nullable(UInt64)'),schema.c('reporting_condition_entries','Array(String)'),
        schema.c('reporting_reason','UInt8')))
    return schema.Table(RAW,tuple(columns),('source_date','ticker','sip_timestamp_us','sequence_number'),'source_date')


def raw_insert_sql(raw,day,*,database='q_live',query_threads=2,query_memory_bytes=2*1024**3):
    """Bind original fields by name; updater projection order is independent."""
    from pipelines.certified_research.contracts import identifier
    identifier(database)
    if type(day) is not date or not isinstance(raw,str) or not raw:
        raise ValueError('Invalid retained raw projection/date')
    if (type(query_threads) is not int or not 1<=query_threads<=8 or type(query_memory_bytes) is not int
            or not 64*1024**2<=query_memory_bytes<=32*1024**3):
        raise ValueError('Invalid retained raw insertion budget')
    names=[c.name for c in raw_table().columns]
    expressions=[f'toDate({literal(day)})' if name=='source_date' else 'r.'+name for name in names]
    return (f'INSERT INTO {database}.{RAW} ({",".join(names)}) SELECT {",".join(expressions)} '
        f'FROM ({raw}) r SETTINGS max_threads={query_threads},max_memory_usage={query_memory_bytes}')


def canonical_table():
    """Temporary original trade keys/fields, read only by owned ingestion."""
    names={'ticker','event_meta','sip_timestamp_us','price_primary_int','price_secondary_int','size_primary','size_secondary',
        'exchange_primary','exchange_secondary','event_date',*(f'condition_token_{i}' for i in range(1,6))}
    columns=(schema.c('source_date','Date'),schema.c('ordinal','UInt64','Delta,ZSTD(1)'),
        *(c for c in raw_table().columns if c.name in names))
    return schema.Table(CANONICAL,columns,('source_date','ticker','ordinal'),'source_date')


def canonical_insert_sql(target,day,first,last,*,database='q_live',query_threads=2,query_memory_bytes=2*1024**3):
    from pipelines.certified_research.contracts import identifier
    identifier(database)
    target_db,target_name=target.split('.');identifier(target_db);identifier(target_name)
    if type(day) is not date or type(query_threads) is not int or not 1<=query_threads<=8:
        raise ValueError('Invalid canonical snapshot date/thread budget')
    if type(query_memory_bytes) is not int or not 64*1024**2<=query_memory_bytes<=32*1024**3:
        raise ValueError('Invalid canonical snapshot memory budget')
    names=[c.name for c in canonical_table().columns]
    expressions=[f'toDate({literal(day)})' if name=='source_date' else name for name in names]
    return (f'INSERT INTO {database}.{CANONICAL} ({",".join(names)}) SELECT {",".join(expressions)} FROM {target} '
        f'WHERE {ns.event_date_predicate(first,last)} AND sip_timestamp_us BETWEEN {first} AND {last} '
        f'AND bitAnd(event_meta,1)=1 SETTINGS max_threads={query_threads},max_memory_usage={query_memory_bytes}')


def publish_evidence(query,day,guard,*,database='q_live',buckets=16,emit=print):
    """Exact multiset readback in bounded buckets; never load all prior days."""
    from pipelines.certified_research.contracts import identifier
    identifier(database)
    if type(day) is not date or type(buckets) is not int or not 1<=buckets<=256:
        raise ValueError('Invalid reporting publication scope')
    names='source_date,ticker,ordinal,sip_timestamp_ns,participant_timestamp_ns,reporting_condition_entries,reporting_reason,source_digest'
    for bucket in range(buckets):
        guard()
        selected=f'source_date=toDate({literal(day)}) AND cityHash64(ticker)%{buckets}={bucket}'
        prior=f'SELECT source_date,ticker,ordinal FROM {database}.{ns.TABLE} WHERE {selected}'
        query(f'INSERT INTO {database}.{ns.TABLE} ({names}) SELECT '+','.join('s.'+c for c in names.split(','))+
            f' FROM (SELECT * FROM {database}.{STAGE} WHERE {selected}) s '
            f'LEFT ANTI JOIN ({prior}) e USING(source_date,ticker,ordinal)',read=False)
        for left,right in ((STAGE,ns.TABLE),(ns.TABLE,STAGE)):
            guard()
            bad=query(f'SELECT count() n FROM (SELECT {names} FROM {database}.{left} WHERE {selected} '
                f'EXCEPT ALL SELECT {names} FROM {database}.{right} WHERE {selected})')[0]
            if int(bad['n']):raise ValueError('Retained original-clock publication differs from reconciled evidence')
        emit(f'Retained reporting {day} | publication={bucket+1}/{buckets}')


def verify_table_columns(query,table):
    actual=query(f"SELECT name,type FROM system.columns WHERE database='q_live' AND table={literal(table.name)} ORDER BY position")
    if [(r['name'],r['type'].replace(' ','')) for r in actual]!=[(c.name,c.type.replace(' ','')) for c in table.columns]:
        raise ValueError('Retained reporting table schema differs: '+table.name)


def file_plan(paths,days,*,reserve_bytes=256*1024**3,reserve_fraction=.10,maximum_chunk_bytes=256*1024**3):
    """Only filesystem metadata leaves G; check free D space before planning."""
    archive_staging.validate_roots(paths.archive,paths.cache)
    space=archive_staging.space_budget(paths.cache,reserve_bytes=reserve_bytes,reserve_fraction=reserve_fraction)
    files={};missing=[]
    for day in days:
        if type(day) is not date:raise ValueError('Retained reporting requires source dates')
        key=Path('trades_v1')/str(day.year)/f'{day.month:02d}'/(str(day)+'.csv.gz')
        backup=paths.archive/key;cached=paths.cache/key
        selected=backup if backup.is_file() else cached
        if not selected.is_file():missing.append(str(day));continue
        files[day]=dict(key=key,source=selected,bytes=selected.stat().st_size,origin=1 if selected==backup else 2)
    chunks=archive_staging.plan_chunks(((day,value['bytes']) for day,value in sorted(files.items())),space,
        maximum_chunk_bytes=maximum_chunk_bytes)
    return dict(files=files,missing=tuple(missing),space=space,**chunks)


def recover(query,paths,inventory,days,claim,stop,*,reserve_bytes=256*1024**3,reserve_fraction=.10,
            maximum_chunk_bytes=256*1024**3,emit=print,query_threads=2,query_memory_bytes=2*1024**3):
    """Sequential certified units inside space-bounded, restart-safe chunks."""
    if not days:return dict(completed=(),reused=())
    if not isinstance(claim,SourceClaim):raise ValueError('Retained reporting recovery requires canonical source ownership')
    if (type(query_threads) is not int or not 1<=query_threads<=8 or type(query_memory_bytes) is not int
            or not 64*1024**2<=query_memory_bytes<=32*1024**3):
        raise ValueError('Invalid retained reporting query budget')
    paths.validate()
    plan=file_plan(paths,days,reserve_bytes=reserve_bytes,reserve_fraction=reserve_fraction,
        maximum_chunk_bytes=maximum_chunk_bytes)
    emit(f"Retained reporting | free={plan['free_bytes']} reserve={plan['reserve_bytes']} "
         f"source_bytes={plan['total_bytes']} chunks={len(plan['chunks'])} missing={len(plan['missing'])}")
    routing=query("SELECT disk_name FROM system.storage_policies ARRAY JOIN disks AS disk_name WHERE policy_name='live_market_ssd'")
    if not routing or any(r['disk_name']!='live_market_ssd' for r in routing):
        raise ValueError('Reporting evidence requires the explicit live_market_ssd policy')
    claim.check()
    for ddl in (ns.ddl('q_live',ns.TABLE),ns.ddl('q_live',STAGE,stage=True),receipt_table().ddl('q_live')):
        query(ddl,read=False)
    schema.verify_auxiliary_storage(query,'q_live',(ns.TABLE,STAGE,RECEIPTS))
    verify_table_columns(query,receipt_table())
    # A certified capture can resume native repair without requiring the
    # original file to remain online. Never adopt unsigned sidecar rows.
    receipt_days=set()
    for day in days:
        rows=query(f'SELECT evidence_hash FROM q_live.{RECEIPTS} WHERE source_date=toDate({literal(day)}) '
            f"AND source_digest={literal(inventory['source_hashes'][str(day)])}")
        if len(rows)>1:raise ValueError('Retained reporting receipt is duplicated')
        if rows:receipt_days.add(day)
    missing=[d for d in plan['missing'] if date.fromisoformat(d) not in receipt_days]
    if missing:raise ValueError('Retained reporting source files are missing: '+','.join(missing[:20]))
    plan.update(archive_staging.plan_chunks(((day,1 if day in receipt_days else plan['files'][day]['bytes'])
        for day in sorted(days)),plan['space'],maximum_chunk_bytes=maximum_chunk_bytes))
    private=paths.cache/'_retained_reporting'
    completed=[];reused=[]
    for batch in plan['chunks']:
        # Recheck after prior CH writes/copies; the original free-space snapshot
        # never authorizes a later chunk against stale headroom.
        fresh=archive_staging.space_budget(paths.cache,reserve_bytes=reserve_bytes,reserve_fraction=reserve_fraction)
        archive_staging.plan_chunks(batch,fresh,maximum_chunk_bytes=maximum_chunk_bytes)
        for day,size in batch:
            if stop.is_set():raise InterruptedError('Retained reporting stopped; evidence retained for resume')
            source=inventory['rows'][day];source_hash=inventory['source_hashes'][str(day)]
            where=f'source_date=toDate({literal(day)}) AND source_digest={literal(source_hash)}'
            def guard():
                claim.check()
                if stop.is_set():raise InterruptedError('Retained reporting stopped; submitted mutations may continue')
                current=canonical_source.inventory(query,day,day,sessions=(day,))
                if current['missing'] or current['incomplete'] or current['source_hashes'].get(str(day))!=source_hash:
                    raise ValueError('Canonical source changed during retained reporting migration')
            guard()
            receipts=query(f'SELECT evidence_hash,file_digest,file_bytes,file_key,source_origin FROM q_live.{RECEIPTS} WHERE {where}')
            if len(receipts)>1:raise ValueError('Retained reporting receipt is duplicated')
            staged_file=None
            if receipts:
                frozen=receipts[0]['evidence_hash'];reused.append(day)
                if native._hash(query,ns.evidence_fingerprint_sql('q_live.'+ns.TABLE,day))!=frozen:
                    raise ValueError('Retained reporting evidence changed after capture')
                expected_key=Path('trades_v1')/str(day.year)/f'{day.month:02d}'/(str(day)+'.csv.gz')
                if receipts[0]['file_key']!=expected_key.as_posix():raise ValueError('Retained receipt file identity differs')
                if int(receipts[0]['source_origin'])==1:
                    candidate=private/expected_key
                    if candidate.is_file():
                        if candidate.stat().st_size!=int(receipts[0]['file_bytes']) or _file_hash(candidate,stop)!=receipts[0]['file_digest']:
                            raise ValueError('Resumed private staged file differs from certified capture')
                        staged_file=candidate
            else:
                info=plan['files'][day]
                if info['origin']==1:
                    staged_file=private/info['key']
                    copied=archive_staging.copy_file(info['source'],staged_file,paths.archive,private,size,
                        stop=stop,reserve_bytes=reserve_bytes,reserve_fraction=reserve_fraction)
                    if copied['status']=='cached':
                        before=info['source'].stat()
                        file_hash=_file_hash(info['source'],stop)
                        staged_hash=_file_hash(staged_file,stop);after=info['source'].stat()
                        if staged_hash!=file_hash or (after.st_size,after.st_mtime_ns)!=(before.st_size,before.st_mtime_ns):
                            raise ValueError('Retained staged copy does not match its backup')
                    elif copied['status']=='staged':file_hash=copied['sha256']
                    else:raise ValueError('Retained backup changed before staging')
                    input_file=staged_file
                else:
                    # Existing D originals are read but never deleted. Freeze
                    # their complete bytes before/after extraction.
                    input_file=info['source'];file_hash=_file_hash(input_file,stop)
                args=updater.parse_args(['--start-date',str(day),'--end-date',str(day),'--skip-bars'])
                args.flatfiles_root_win=paths.cache
                args.flatfiles_root_ch=paths.clickhouse_cache
                args.events_table='events_'+str(day.year)
                job=DownloadJob('trades',str(day),str(info['key']),str(input_file),size)
                files=updater.DayFiles(str(day),job,job) # Trade-only SQL opens no quote file.
                target='market_sip_compact.'+args.events_table
                fields=['sip_timestamp_us','price_primary_int','price_secondary_int','size_primary','size_secondary',
                    'exchange_primary','exchange_secondary']+[f'condition_token_{n}' for n in range(1,6)]+['event_date']
                raw=updater.raw_event_union_sql(args,files,trade_only=True,
                    include_reporting_reason=True,include_reporting_evidence=True)
                # Budget in uncompressed semantic bytes, with room for staging,
                # publication and merge work; do not infer it from gzip size.
                footprint=query('SELECT count() n,sum(length(ticker)+128+arraySum(arrayMap(x -> length(x)+9,reporting_condition_entries))) bytes '
                    f'FROM ({raw}) SETTINGS max_threads={query_threads},max_memory_usage={query_memory_bytes}')[0]
                fresh=archive_staging.space_budget(paths.cache,reserve_bytes=reserve_bytes,reserve_fraction=reserve_fraction)
                if int(footprint['n'])!=int(source['trade_event_rows']):raise ValueError('Retained trade count differs from canonical source')
                if 4*int(footprint['bytes'])+256*1024**2>fresh['usable_bytes']:
                    raise ValueError('Retained evidence staging/publication would consume reserved SSD headroom')
                stage_where=f'source_date=toDate({literal(day)})'
                settings=f'max_threads={query_threads},max_memory_usage={query_memory_bytes}'
                # After the sizing pass, materialize once; subsequent
                # reconciliation reads only SSD CH, never the gzip again.
                guard();query(raw_table().ddl('q_live'),read=False)
                schema.verify_auxiliary_storage(query,'q_live',(RAW,))
                verify_table_columns(query,raw_table())
                query(f'ALTER TABLE q_live.{RAW} DELETE WHERE {stage_where} SETTINGS mutations_sync=2',read=False)
                query(raw_insert_sql(raw,day,query_threads=query_threads,query_memory_bytes=query_memory_bytes),read=False)
                raw_count=query(f'SELECT count() n FROM q_live.{RAW} WHERE {stage_where}')[0]['n']
                if int(raw_count)!=int(source['trade_event_rows']):raise ValueError('SSD raw staging count differs from canonical trades')
                # Read the canonical month once, then reuse its compact trade
                # snapshot for every bucket. Never reread the archive for joins.
                guard();query(canonical_table().ddl('q_live'),read=False)
                schema.verify_auxiliary_storage(query,'q_live',(CANONICAL,));verify_table_columns(query,canonical_table())
                query(f'ALTER TABLE q_live.{CANONICAL} DELETE WHERE {stage_where} SETTINGS mutations_sync=2',read=False)
                query(canonical_insert_sql(target,day,int(source['first_sip_timestamp_us']),int(source['last_sip_timestamp_us']),
                    query_threads=query_threads,query_memory_bytes=query_memory_bytes),read=False)
                if int(query(f'SELECT count() n FROM q_live.{CANONICAL} WHERE {stage_where}')[0]['n'])!=int(source['trade_event_rows']):
                    raise ValueError('Canonical trade snapshot count differs from certified source')
                guard();query(f'ALTER TABLE q_live.{STAGE} DELETE WHERE {stage_where} SETTINGS mutations_sync=2',read=False)
                for bucket in range(16):
                    guard()
                    bounded=f'SELECT * FROM q_live.{RAW} WHERE {stage_where} AND cityHash64(ticker)%16={bucket}'
                    projection=ns.reconciled_projection(bounded,'q_live.'+CANONICAL,day,source_hash,fields,
                        first_sip_us=int(source['first_sip_timestamp_us']),last_sip_us=int(source['last_sip_timestamp_us']),
                        repair_reporting_flags=True,ticker_bucket=(bucket,16),canonical_stage=True)
                    projection+=f',{settings},max_bytes_before_external_sort={query_memory_bytes//4}'
                    query(f'INSERT INTO q_live.{STAGE} '+projection,read=False)
                    emit(f'Retained reporting {day} | reconcile={bucket+1}/16')
                audit=query(f'SELECT sum(n) n,sum(unique_keys) unique_keys,sum(bad) bad FROM ('
                    f'SELECT ticker,count() n,uniqExact(ordinal) unique_keys,countIf(mismatch OR ambiguous) bad '
                    f'FROM q_live.{STAGE} WHERE {stage_where} GROUP BY ticker) SETTINGS optimize_aggregation_in_order=1')[0]
                expected=int(source['trade_event_rows'])
                if int(audit['n'])!=expected or int(audit['unique_keys'])!=expected or int(audit['bad']):
                    raise ValueError('Retained reporting reconstruction is incomplete, mismatched or ambiguous')
                if _file_hash(input_file,stop)!=file_hash:raise ValueError('Retained reporting input file changed during extraction')
                # Fill only absent keys after complete source reconciliation.
                publish_evidence(query,day,guard,emit=emit)
                frozen=native._hash(query,ns.evidence_fingerprint_sql('q_live.'+ns.TABLE,day))
                guard();insert_rows(query,'q_live',RECEIPTS,[dict(source_date=str(day),source_digest=source_hash,evidence_hash=frozen,
                    file_digest=file_hash,file_bytes=size,file_key=str(info['key']).replace('\\','/'),source_origin=info['origin'],
                    created_at=datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f'))])
                readback=query(f'SELECT evidence_hash,file_digest,file_bytes,file_key,source_origin FROM q_live.{RECEIPTS} WHERE {where}')
                expected_receipt=dict(evidence_hash=frozen,file_digest=file_hash,file_bytes=size,
                    file_key=str(info['key']).replace('\\','/'),source_origin=info['origin'])
                if len(readback)!=1 or readback[0]!=expected_receipt:
                    raise ValueError('Retained acquisition receipt did not read back exactly')
            while True:
                guard();result=native.step(query,source,frozen,claim,guard)
                emit(f"Retained reporting {day} | {result['status']} | parts={result.get('parts_remaining',0)}")
                if result['status']=='complete':break
                if stop.wait(1):raise InterruptedError('Retained reporting stopped; mutation retained for resume')
            schema.verify_auxiliary_storage(query,'q_live',(ns.TABLE,STAGE,RECEIPTS))
            query(f'ALTER TABLE q_live.{STAGE} DELETE WHERE source_date=toDate({literal(day)}) SETTINGS mutations_sync=2',read=False)
            if query(f"SELECT count() n FROM system.tables WHERE database='q_live' AND name={literal(RAW)}")[0]['n']:
                query(f'ALTER TABLE q_live.{RAW} DELETE WHERE source_date=toDate({literal(day)}) SETTINGS mutations_sync=2',read=False)
            if query(f"SELECT count() n FROM system.tables WHERE database='q_live' AND name={literal(CANONICAL)}")[0]['n']:
                query(f'ALTER TABLE q_live.{CANONICAL} DELETE WHERE source_date=toDate({literal(day)}) SETTINGS mutations_sync=2',read=False)
            if staged_file is not None:
                resolved=staged_file.resolve()
                if not resolved.is_relative_to(private.resolve()):raise ValueError('Owned staging cleanup escaped its D root')
                resolved.unlink()
            completed.append(day)
    return dict(completed=tuple(completed),reused=tuple(reused))


def _file_hash(path,stop):
    from hashlib import sha256
    result=sha256()
    with Path(path).open('rb') as stream:
        while chunk:=stream.read(8*1024**2):
            if stop.is_set():raise InterruptedError('Retained reporting stopped during file verification')
            result.update(chunk)
    return result.hexdigest()
