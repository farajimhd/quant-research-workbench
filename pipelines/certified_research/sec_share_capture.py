"""Normalized SSD capture of SEC source candidates, never reference receipts."""
import json
from . import schema,sec_document_lineage
from .contracts import digest,identifier,literal
from .publication import insert_rows,DictionaryClaim

VERSION='sec-share-source-capture-v1'
PREFIX='research_sec_share_source_'


def tables():
    c=schema.c
    common=(c('document_key','FixedString(64)'),c('source_archive_date','Date'))
    return (
        schema.Table(PREFIX+'document_v1',common+(c('cik','UInt64','T64,ZSTD(1)'),
            c('prepared_hash','FixedString(64)'),c('source_text_hash','FixedString(64)'),
            c('parser_version','LowCardinality(String)'),c('source_bytes','UInt32','T64,ZSTD(1)'),
            c('contexts','UInt32','T64,ZSTD(1)'),c('observations','UInt32','T64,ZSTD(1)'),
            c('proof','String')),('document_key',),'toYYYYMM(source_archive_date)'),
        schema.Table(PREFIX+'context_v1',common+(c('context_key','FixedString(64)'),
            c('context_id','LowCardinality(String)'),c('period_end','Date'),
            c('dimensions','Array(Tuple(String,String))')),('document_key','context_key'),'toYYYYMM(source_archive_date)'),
        schema.Table(PREFIX+'observation_v1',common+(c('source_position','UInt32','Delta,ZSTD(1)'),
            c('context_key','Nullable(FixedString(64))'),c('fact_id','String'),c('concept','LowCardinality(String)'),
            c('unit_id','LowCardinality(String)'),c('decimals','LowCardinality(String)'),
            c('scale','LowCardinality(String)'),c('format','LowCardinality(String)'),
            c('value','Nullable(String)'),c('extraction_status','LowCardinality(String)'),
            c('reason','LowCardinality(String)'),c('rejected_context_id','Nullable(String)')),
            ('document_key','source_position'),'toYYYYMM(source_archive_date)'))


def verify(query,database,*,required=False):
    identifier(database);catalog={t.name:t for t in tables()};names=','.join(map(literal,catalog))
    if database!='q_live' and not database.startswith('certified_research_test_'):
        raise ValueError('SEC source captures require q_live or an isolated fixture namespace')
    policies=query("SELECT disk_name FROM system.storage_policies ARRAY JOIN disks AS disk_name WHERE policy_name='live_market_ssd'")
    if not policies or any(r['disk_name']!='live_market_ssd' for r in policies):raise ValueError('SEC capture SSD policy is unavailable')
    rows=query('SELECT name,engine,sorting_key,partition_key,storage_policy FROM system.tables '
        f'WHERE database={literal(database)} AND name IN ({names})')
    if required and {r['name'] for r in rows}!=set(catalog):raise ValueError('SEC capture schemas are missing')
    compact=lambda value:value.replace(' ','').replace('`','')
    for row in rows:
        t=catalog[row['name']]
        if (row['engine']!='MergeTree' or row['storage_policy']!='live_market_ssd'
                or compact(row['sorting_key'])!=','.join(t.order) or compact(row['partition_key'])!=t.partition):
            raise ValueError('SEC capture engine/key/policy differs; explicit migration required')
    cols=query('SELECT table,name,type,compression_codec FROM system.columns '
        f'WHERE database={literal(database)} AND table IN ({names})')
    actual={(r['table'],r['name']):(compact(r['type']),compact(r['compression_codec'])) for r in cols}
    present={r['name'] for r in rows}
    expected={(t.name,c.name):(compact(c.type),'CODEC('+compact(c.codec)+')') for t in catalog.values() if t.name in present for c in t.columns}
    if actual!=expected:raise ValueError('SEC capture columns/codecs differ; explicit migration required')
    parts=query('SELECT table,disk_name FROM system.parts '
        f'WHERE database={literal(database)} AND table IN ({names}) GROUP BY table,disk_name')
    if any(r['disk_name']!='live_market_ssd' for r in parts):raise ValueError('SEC capture active/inactive parts are misplaced')


def install(query,database,claim):
    if not isinstance(claim,DictionaryClaim) or claim.dictionary_database!=database:
        raise ValueError('SEC capture installation requires global database dictionary ownership')
    claim.check();verify(query,database)
    for table in tables():claim.check();query(table.ddl(database),read=False)
    verify(query,database,required=True);claim.check()


def records(prepared):
    body=dict(prepared);hash_=body.pop('prepared_hash',None)
    if (hash_!=digest(body) or prepared.get('candidate_only') is not True
            or prepared.get('original_vintage_qualified') is not False or prepared.get('allocation_certified') is not False):
        raise ValueError('SEC source candidate fingerprint/status differs')
    source=prepared['document'];day=source['lineage']['document']['source_archive_date'];key=source['document_key']
    contexts=tuple(dict(row,source_archive_date=day) for row in prepared['contexts'])
    observations=tuple(dict(row,source_archive_date=day) for row in prepared['observations'])
    if len(contexts)>10000 or len(observations)>10000:raise ValueError('SEC capture exceeds source bounds')
    keys={r['context_key'] for r in contexts}
    if len(keys)!=len(contexts) or len({r['source_position'] for r in observations})!=len(observations):
        raise ValueError('SEC capture has competing normalized keys')
    for row in prepared['contexts']:
        body=dict(row);context_key=body.pop('context_key')
        if row['document_key']!=key or context_key!=digest(body):raise ValueError('SEC context fingerprint differs')
    for row in observations:
        if (row['document_key']!=key or (row['extraction_status']=='observed' and
                (row['context_key'] not in keys or row['value'] is None or row['reason']))
                or (row['extraction_status']=='rejected' and (row['context_key'] is not None or row['value'] is not None or not row['reason']))
                or row['extraction_status'] not in ('observed','rejected')):
            raise ValueError('SEC capture context/value status relationship differs')
    header=dict(document_key=key,source_archive_date=day,cik=int(source['cik']),prepared_hash=hash_,
        source_text_hash=source['source_text_hash'],parser_version=source['parser_version'],source_bytes=source['source_bytes'],
        contexts=len(contexts),observations=len(observations),proof=json.dumps(source,sort_keys=True,separators=(',',':')))
    return (header,),contexts,observations


def capture(query,database,prepared,claim,source_guard,*,admission):
    """Append missing exact rows, independently read back, then capture header.

    ``admission`` must qualify the bounded source write's current headroom.
    This header is never a research_reference_receipt or allocation authority.
    """
    if not callable(admission):raise ValueError('SEC capture requires source-write admission')
    expected=records(prepared);catalog=tables();key=prepared['document']['document_key']
    metadata_bytes=sum(len(json.dumps(row,separators=(',',':')).encode()) for group in expected for row in group)
    if metadata_bytes>64*1024**2:raise ValueError('SEC normalized source metadata exceeds its byte bound')
    def guard(*,writing=False):
        claim.check();source_guard();claim.check();verify(query,database,required=True)
        if writing:
            certificate=admission(metadata_bytes)
            if not isinstance(certificate,dict) or certificate.get('status')!='admitted' or certificate.get('metadata_bytes')!=metadata_bytes:
                raise ValueError('SEC source-write admission differs from the bounded metadata request')
        claim.check()
    def current_lineage():
        original=prepared['document']['lineage'];scope=original['document']
        current=sec_document_lineage.inspect(query,scope['cik'],scope['accession_number'],scope['document_name'],claim,source_guard)
        if current!=original:raise ValueError('SEC document changed before candidate capture')
    def normal(row,table):
        row=dict(row)
        for col in table.columns:
            if col.type=='UInt64':row[col.name]=int(row[col.name])
        return json.loads(json.dumps(row,sort_keys=True))
    def read(table):
        rows=query(f'SELECT {",".join(c.name for c in table.columns)} FROM {database}.{table.name} '
            f'WHERE document_key={literal(key)} ORDER BY {",".join(table.order)} LIMIT 10001')
        if len(rows)>10000:raise ValueError('SEC capture readback exceeds its bound')
        return [normal(row,table) for row in rows]
    guard();current_lineage();inserted=0
    headers=read(catalog[0])
    if headers:
        for table,group in zip(catalog,expected):
            wanted=sorted((normal(row,table) for row in group),key=lambda row:tuple(row[k] for k in table.order))
            if read(table)!=wanted:raise ValueError('Completed SEC candidate capture changed; explicit reconciliation required')
        current_lineage();guard()
        return dict(version=VERSION,document_key=key,prepared_hash=prepared['prepared_hash'],inserted_rows=0,
            candidate_only=True,original_vintage_qualified=False,allocation_certified=False)
    # Contexts/facts first. The immutable document header exposes a complete
    # candidate capture only after both tables independently match expectations.
    for index in (1,2,0):
        table=catalog[index];wanted={tuple(row[k] for k in table.order):normal(row,table) for row in expected[index]}
        found=read(table);seen={}
        for row in found:
            identity=tuple(row[k] for k in table.order)
            if identity in seen or wanted.get(identity)!=row:raise ValueError('SEC capture conflicts with retained rows')
            seen[identity]=row
        missing=[row for identity,row in wanted.items() if identity not in seen]
        if index==0:current_lineage()
        for offset in range(0,len(missing),1024):
            guard(writing=True);insert_rows(query,database,table.name,missing[offset:offset+1024]);inserted+=len(missing[offset:offset+1024])
        if read(table)!=sorted(wanted.values(),key=lambda row:tuple(row[k] for k in table.order)):
            raise ValueError('SEC candidate capture readback differs')
    current_lineage();guard()
    return dict(version=VERSION,document_key=key,prepared_hash=prepared['prepared_hash'],inserted_rows=inserted,
        candidate_only=True,original_vintage_qualified=False,allocation_certified=False)
