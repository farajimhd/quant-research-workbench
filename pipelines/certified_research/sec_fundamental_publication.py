"""Normalized original fundamental evidence, not whole-period certification."""
import json
from datetime import datetime,timezone

from . import schema,sec_share_capture,sec_fundamental_reconciliation
from .contracts import digest,literal
from .publication import insert_rows,DictionaryClaim

PREFIX='research_sec_fundamental_source_'


def tables():
    c=schema.c;common=(c('extraction_hash','FixedString(64)'),c('source_archive_date','Date'))
    return (
        schema.Table(PREFIX+'context_v2',common+(c('context_hash','FixedString(64)'),c('unit','LowCardinality(String)'),
            c('period_start','Nullable(Date)'),c('period_end','Date'),c('dimensions','Array(Tuple(String,String))'),
            ),('extraction_hash','context_hash'),'toYYYYMM(source_archive_date)'),
        schema.Table(PREFIX+'observation_v2',common+(c('source_position','UInt32','Delta,ZSTD(1)'),
            c('context_hash','FixedString(64)'),c('concept','LowCardinality(String)'),c('value','String'),c('proof','String')),
            ('extraction_hash','source_position'),'toYYYYMM(source_archive_date)'),
        schema.Table(PREFIX+'comparison_v2',common+(c('reconciliation_hash','FixedString(64)'),c('source_record','String'),c('status','LowCardinality(String)'),
            c('context_hash','FixedString(64)'),c('source_value','String'),c('original_positions','Array(UInt32)')),
            ('reconciliation_hash','source_record'),'toYYYYMM(source_archive_date)'),
        schema.Table(PREFIX+'document_v2',common+(c('document_key','FixedString(64)'),c('document_hash','FixedString(64)'),
            c('catalog_hash','FixedString(64)'),c('witness_hash','FixedString(64)'),c('qualification_hash','FixedString(64)'),
            c('reconciliation_hash','FixedString(64)'),c('available_at',"DateTime64(6,'UTC')",'Delta,ZSTD(1)'),
            c('precision','UInt8'),c('observations','UInt32','T64,ZSTD(1)'),c('comparisons','UInt32','T64,ZSTD(1)'),
            c('proof','String')),('reconciliation_hash',),'toYYYYMM(source_archive_date)'))


def records(extraction,bulk,witness,qualification,receipt):
    result=sec_fundamental_reconciliation.reconcile(extraction,bulk,witness,qualification,receipt)
    common=dict(extraction_hash=extraction['extraction_hash'],source_archive_date=witness['source_archive_date'])
    observations=[];contexts={}
    for row in extraction['observations']:
        context=(row['period_start'],row['period_end'],row['unit'],row['dimensions'])
        context_hash=digest(context)
        contexts[context_hash]=dict(common,context_hash=context_hash,unit=row['unit'],period_start=row['period_start'],
            period_end=row['period_end'],dimensions=row['dimensions'])
        observations.append(dict(common,source_position=row['source_position'],context_hash=context_hash,concept=row['concept'],value=row['value'],
            proof=json.dumps({k:v for k,v in row.items() if k not in ('concept','unit','period_start','period_end','dimensions','value')},
                sort_keys=True,separators=(',',':'))))
    comparisons=[dict(common,reconciliation_hash=result['reconciliation_hash'],**{k:v for k,v in row.items() if k!='original_values'}) for row in result['comparisons']]
    proof={k:v for k,v in result.items() if k!='comparisons'}
    proof['extraction_metadata']={k:v for k,v in extraction.items() if k not in ('observations','rejected')}
    header=dict(common,document_key=witness['document_key'],document_hash=witness['document_hash'],catalog_hash=extraction['catalog_hash'],
        witness_hash=witness['witness_hash'],qualification_hash=qualification['qualification_hash'],reconciliation_hash=result['reconciliation_hash'],
        available_at=datetime.fromisoformat(qualification['available_at']).astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f'),
        precision=qualification['precision'],observations=len(observations),comparisons=len(comparisons),
        proof=json.dumps(proof,sort_keys=True,separators=(',',':')))
    return tuple(contexts.values()),tuple(observations),tuple(comparisons),(header,)


def install(query,database,claim):
    if not isinstance(claim,DictionaryClaim) or claim.dictionary_database!=database:
        raise ValueError('Fundamental evidence schema requires dictionary ownership')
    claim.check();sec_share_capture.verify(query,database,catalog=tables())
    for table in tables():claim.check();query(table.ddl(database),read=False)
    sec_share_capture.verify(query,database,required=True,catalog=tables());claim.check()


def capture(query,database,extraction,bulk,witness,qualification,receipt,claim,source_guard,admission):
    groups=records(extraction,bulk,witness,qualification,receipt);catalog=tables()
    scopes={table.name:(('reconciliation_hash',groups[-1][0]['reconciliation_hash']) if any(c.name=='reconciliation_hash' for c in table.columns)
        else ('extraction_hash',extraction['extraction_hash'])) for table in catalog}
    result=capture_groups(query,database,catalog,groups,scopes,claim,source_guard,admission)
    return dict(result,extraction_hash=extraction['extraction_hash'],reconciliation_hash=groups[-1][0]['reconciliation_hash'],
        certified=False,allocation_certified=False)


def capture_groups(query,database,catalog,groups,scopes,claim,source_guard,admission):
    """Shared bounded immutable SEC publication; completion header is last."""
    if not callable(admission):raise ValueError('Fundamental evidence requires measured source-write admission')
    if (not 1<=len(catalog)<=16 or len(groups)!=len(catalog) or len({t.name for t in catalog})!=len(catalog)
            or set(scopes)!={t.name for t in catalog} or len(groups[-1])!=1):
        raise ValueError('Invalid bounded immutable SEC publication catalog')
    for table,group in zip(catalog,groups):
        key,value=scopes[table.name]
        if key not in table.order or len(group)>10000 or any(row[key]!=value for row in group):
            raise ValueError('Immutable SEC publication scope/row bound differs')
    metadata_bytes=sum(len(json.dumps(row,separators=(',',':')).encode()) for group in groups for row in group)
    if metadata_bytes>64*1024**2:raise ValueError('Fundamental evidence exceeds 64MiB metadata bound')
    def guard():
        claim.check();source_guard();claim.check()
        sec_share_capture.verify(query,database,required=True,catalog=catalog)
    def normalized(row):
        row=dict(row)
        if 'dimensions' in row:row['dimensions']=tuple(tuple(pair) for pair in row['dimensions'])
        if 'original_positions' in row:row['original_positions']=tuple(row['original_positions'])
        if 'all_source_positions' in row:row['all_source_positions']=tuple(row['all_source_positions'])
        return row
    def read(table):
        key,value=scopes[table.name]
        return [normalized(r) for r in query(f'SELECT {",".join(c.name for c in table.columns)} '
            f'FROM {database}.{table.name} WHERE {key}={literal(value)} LIMIT 10001')]
    guard();existing=[read(table) for table in catalog];inserted=0
    complete=bool(existing[-1])
    for table,expected,current in zip(catalog,groups,existing):
        by_key={tuple(row[k] for k in table.order):row for row in current}
        desired={tuple(row[k] for k in table.order):normalized(row) for row in expected}
        if len(by_key)!=len(current) or len(desired)!=len(expected) or any(desired.get(k)!=row for k,row in by_key.items()):
            raise ValueError('Fundamental evidence conflicts with immutable retained rows')
        missing=[row for k,row in desired.items() if k not in by_key]
        if complete and missing:raise ValueError('Completed fundamental evidence has missing rows; explicit reconciliation required')
        for offset in range(0,len(missing),256):
            guard();accepted=admission(metadata_bytes)
            if not isinstance(accepted,dict) or accepted.get('status')!='admitted' or accepted.get('metadata_bytes')!=metadata_bytes:
                raise ValueError('Fundamental source-write admission differs')
            guard();batch=missing[offset:offset+256];insert_rows(query,database,table.name,batch);inserted+=len(batch)
        actual=read(table)
        if len(actual)!=len(desired) or {tuple(r[k] for k in table.order):r for r in actual}!=desired:
            raise ValueError('Fundamental evidence publication readback differs')
    guard()
    for table,expected in zip(catalog,groups):
        desired={tuple(row[k] for k in table.order):normalized(row) for row in expected}
        actual=read(table)
        if len(actual)!=len(desired) or {tuple(r[k] for k in table.order):r for r in actual}!=desired:
            raise ValueError('Fundamental final group readback differs')
    return dict(persisted=True,readback_verified=True,inserted_rows=inserted)
