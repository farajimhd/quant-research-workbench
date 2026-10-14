"""Ingestion-owned qualification of SEC acquisition stage lineage, not values."""
from datetime import date,datetime,timedelta,timezone
from . import schema
from .contracts import digest,identifier,literal
from .publication import insert_rows
from pipelines.sec.edgar import sec_acquisition_contract as contract

TABLE='sec_acquisition_certificate_v1'
RAW_TABLE='sec_coverage_manifest_v3'
FIELDS=('coverage_id','source','coverage_kind','coverage_start_utc','coverage_end_utc','status','row_count',
    'file_count','error_count','run_id','host_role','metadata_json','started_at_utc','updated_at_utc','completed_at_utc')


def table():
    return schema.Table(TABLE,(schema.c('certificate_hash','FixedString(64)'),schema.c('source_coverage_id','String'),
        schema.c('coverage_kind','LowCardinality(String)'),
        schema.c('coverage_start_utc',"DateTime64(3,'UTC')",'Delta,ZSTD(1)'),
        schema.c('coverage_end_utc',"DateTime64(3,'UTC')",'Delta,ZSTD(1)'),
        schema.c('source_proof_hash','FixedString(64)'),schema.c('stage_witnesses','Array(Tuple(String,String,FixedString(64)))'),
        schema.c('contract_version','LowCardinality(String)'),schema.c('certified_at_utc',"DateTime64(6,'UTC')",'Delta,ZSTD(1)')),
        ('coverage_kind','coverage_start_utc','source_coverage_id','source_proof_hash'),'toYYYYMM(coverage_start_utc)')


def verify_table(query,database):
    definition=table();schema.verify_auxiliary_storage(query,database,(TABLE,))
    rows=query(f'SELECT engine,sorting_key,partition_key FROM system.tables WHERE database={literal(database)} AND name={literal(TABLE)}')
    compact=lambda value:value.replace(' ','').replace('`','')
    if (len(rows)!=1 or rows[0]['engine']!='MergeTree' or compact(rows[0]['sorting_key'])!=','.join(definition.order)
            or compact(rows[0]['partition_key'])!=compact(definition.partition)):
        raise ValueError('SEC certificate engine/key/partition differs; explicit migration required')
    actual=query(f'SELECT name,type,compression_codec FROM system.columns WHERE database={literal(database)} '
        f'AND table={literal(TABLE)} ORDER BY position')
    expected=[(c.name,c.type.replace(' ',''),'CODEC('+c.codec.replace(' ','')+')') for c in definition.columns]
    if [(r['name'],r['type'].replace(' ',''),r['compression_codec'].replace(' ','')) for r in actual]!=expected:
        raise ValueError('SEC certificate schema/codec differs; explicit migration required')


def inputs(query,start,end,*,database='q_live',maximum_rows=16384):
    identifier(database)
    if type(start) is not date or type(end) is not date or start>end or not 1<=maximum_rows<=65536:
        raise ValueError('Invalid bounded SEC lineage range')
    schema.verify_auxiliary_storage(query,database,(RAW_TABLE,))
    names=','.join(literal(n) for n in contract.REQUIRED)
    rows=query(f'SELECT {",".join(FIELDS)},lower(hex(SHA256(toJSONString(tuple({",".join(FIELDS)}))))) proof_hash '
        f'FROM {database}.{RAW_TABLE} FINAL WHERE source=\'sec\' '
        f'AND (coverage_kind IN ({names}) OR startsWith(coverage_kind,\'sec_stage_\')) '
        f"AND coverage_start_utc<toDateTime64({literal(end+timedelta(days=1))},3,'UTC') "
        f"AND coverage_end_utc>toDateTime64({literal(start)},3,'UTC') LIMIT {maximum_rows+1}")
    if len(rows)>maximum_rows:raise ValueError('SEC lineage metadata exceeds its bound')
    ids=[r['coverage_id'] for r in rows]
    if any(not value for value in ids) or len(ids)!=len(set(ids)):raise ValueError('SEC lineage identities are missing or duplicated')
    stages=[]
    for row in rows:
        if row['coverage_kind'].startswith('sec_stage_'):
            value=contract.stage(row)
            if value is not None:stages.append(value)
    return dict(parents=tuple(r for r in rows if r['coverage_kind'] in contract.REQUIRED),stages=tuple(stages),
        hash=digest(sorted((r['coverage_id'],r['proof_hash']) for r in rows)))


def binding(parent,witnesses):
    if not witnesses:raise ValueError('SEC acquisition certificate requires complete stage witnesses')
    value=dict(source_coverage_id=parent['coverage_id'],coverage_kind=parent['coverage_kind'],
        coverage_start_utc=contract.utc(parent['coverage_start_utc']).isoformat(timespec='milliseconds'),
        coverage_end_utc=contract.utc(parent['coverage_end_utc']).isoformat(timespec='milliseconds'),
        source_proof_hash=parent['proof_hash'],stage_witnesses=tuple(witnesses),contract_version=contract.VERSION)
    return dict(value,certificate_hash=digest(value))


def verify(row,parent,stages):
    recorded=tuple(tuple(w) for w in row['stage_witnesses'])
    selected=[];by_id={s['id']:s for s in stages}
    for name,id_,hash_ in recorded:
        actual=by_id.get(id_)
        if actual is None or actual['name']!=name or actual['hash']!=hash_:raise ValueError('SEC stage witness changed or is missing')
        selected.append(actual)
    if contract.witnesses(parent,selected)!=recorded:raise ValueError('SEC stage witnesses do not cover their parent range')
    expected=binding(parent,recorded)
    actual={k:row[k] for k in expected}
    for key in ('coverage_start_utc','coverage_end_utc'):actual[key]=contract.utc(actual[key]).isoformat(timespec='milliseconds')
    actual['stage_witnesses']=recorded
    if actual!=expected:raise ValueError('SEC acquisition certificate differs from its immutable binding')
    return expected


def certificates(query,start,end,*,database='q_live',maximum_rows=16384):
    exists=query(f'SELECT count() n FROM system.tables WHERE database={literal(database)} AND name={literal(TABLE)}')
    if len(exists)!=1 or not int(exists[0]['n']):return ()
    verify_table(query,database)
    rows=query(f'SELECT * FROM {database}.{TABLE} WHERE '
        f"coverage_start_utc<toDateTime64({literal(end+timedelta(days=1))},3,'UTC') "
        f"AND coverage_end_utc>toDateTime64({literal(start)},3,'UTC') LIMIT {maximum_rows+1}")
    if len(rows)>maximum_rows:raise ValueError('SEC certificates exceed metadata bound')
    return tuple(rows)


def qualified(query,start,end,*,database='q_live'):
    """Read-only authority check; old labels never become proof by themselves."""
    snapshot=inputs(query,start,end,database=database);parents={r['coverage_id']:r for r in snapshot['parents']};valid={}
    seen=set()
    for row in certificates(query,start,end,database=database):
        key=(row['source_coverage_id'],row['source_proof_hash'])
        if key in seen:raise ValueError('SEC acquisition certificates are duplicated')
        seen.add(key);parent=parents.get(key[0])
        if parent is None or parent['proof_hash']!=key[1]:continue # Preserved older source version.
        verify(row,parent,snapshot['stages']);valid[key[0]]=row['certificate_hash']
    return dict(snapshot,qualified=valid)


def qualify(query,claim,start,end,*,database='q_live',write=True,stop=None,progress=None):
    """Metadata-only, idempotent promotion; source manifests remain untouched."""
    def guard():
        if stop is not None and stop.is_set():raise InterruptedError('SEC lineage qualification stopped')
        claim.check()
    guard();snapshot=qualified(query,start,end,database=database)
    if write:
        definition=table();query(definition.ddl(database),read=False)
        verify_table(query,database)
    completed=[];missing=[]
    for parent in snapshot['parents']:
        guard();key=parent['coverage_id']
        if key in snapshot['qualified']:completed.append(key);continue
        witnesses=contract.witnesses(parent,snapshot['stages'])
        if witnesses is None:missing.append(key);continue
        row=binding(parent,witnesses)
        if write:
            if inputs(query,start,end,database=database)['hash']!=snapshot['hash']:
                raise ValueError('SEC stage/source metadata changed during qualification')
            row['certified_at_utc']=datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f')
            stored=dict(row)
            for column in ('coverage_start_utc','coverage_end_utc'):
                stored[column]=contract.utc(stored[column]).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
            insert_rows(query,database,TABLE,[stored])
        completed.append(key)
        if progress:progress(dict(phase='sec-lineage',qualified=len(completed),unqualified=len(missing),total=len(snapshot['parents'])))
    guard()
    after=qualified(query,start,end,database=database)
    if after['hash']!=snapshot['hash']:raise ValueError('SEC metadata changed before qualification readback')
    if write and any(key not in after['qualified'] for key in completed):raise ValueError('SEC qualification readback failed')
    return dict(status=('qualified' if write else 'planned') if not missing else 'incomplete',
        qualified=tuple(key for key in completed if key in after['qualified']),eligible=tuple(completed),unqualified=tuple(missing),
        source_hash=snapshot['hash'],contract_version=contract.VERSION,write=write)
