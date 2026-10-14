"""Ingestion-owned original tape evidence; never infer NYSE from encoded zero."""
from datetime import date
from pipelines.certified_research import schema
from pipelines.certified_research.contracts import identifier,literal
from pipelines.certified_research.publication import insert_rows
from . import native_reporting_repair as native,reporting_evidence as ns

TABLE='historical_original_trade_tape_v1'
RECEIPTS='historical_original_trade_tape_coverage_v1'
VERSION=1
FIELDS='source_date,ticker,ordinal,original_listing_tape'


def tables():
    records=schema.Table(TABLE,(schema.c('source_date','Date'),schema.c('ticker','LowCardinality(String)'),
        schema.c('ordinal','UInt64','Delta,ZSTD(1)'),schema.c('original_listing_tape','Nullable(UInt8)')),
        ('source_date','ticker','ordinal'),'toYYYYMM(source_date)')
    receipts=schema.Table(RECEIPTS,(schema.c('source_date','Date'),schema.c('source_digest','FixedString(64)'),
        schema.c('clock_evidence_hash','FixedString(64)'),schema.c('file_digest','FixedString(64)'),
        schema.c('tape_evidence_hash','FixedString(64)'),schema.c('trade_rows','UInt64','T64,ZSTD(1)'),
        schema.c('version','UInt8')),('source_date','source_digest'),'toYYYYMM(source_date)')
    return records,receipts


def fingerprint_sql(database,day):
    identifier(database)
    return native._fingerprint(f'SELECT {FIELDS} FROM {database}.{TABLE} WHERE source_date=toDate({literal(day)})',FIELDS)


def inventory(query,inventory):
    """Bounded receipts; absence is explicit, never a zero-tape NYSE fallback."""
    exists=query(f"SELECT count() n FROM system.tables WHERE database='q_live' AND name={literal(RECEIPTS)}")[0]['n']
    if not exists:return tuple(inventory['expected'])
    schema.verify_auxiliary_storage(query,'q_live',(TABLE,RECEIPTS))
    days=inventory['expected']
    rows=query(f'SELECT * FROM q_live.{RECEIPTS} WHERE source_date BETWEEN toDate({literal(min(days))}) '
        f'AND toDate({literal(max(days))})')
    seen={}
    for row in rows:
        d=date.fromisoformat(str(row['source_date']))
        if d not in inventory['rows']:continue
        if d in seen:raise ValueError('Original tape receipt is duplicated')
        if (row['source_digest']!=inventory['source_hashes'][str(d)] or int(row['version'])!=VERSION
                or int(row['trade_rows'])!=int(inventory['rows'][d]['trade_event_rows'])):
            raise ValueError('Original tape receipt differs from its canonical source')
        seen[d]=row
    return tuple(d for d in days if d not in seen)


def prior_projection(raw,target,day,source_digest,*,bucket,total=16):
    """Reuse a byte-identical previously reconciled acquisition, not prices again.

    Every original clock/full-condition record and trade rank must match the
    frozen NS association; ambiguous or missing ranks still block publication.
    The caller verifies the original full file digest and frozen NS fingerprint.
    """
    db,name=target.split('.');identifier(db);identifier(name)
    if not 0<=bucket<total<=256:raise ValueError('Invalid original tape bucket')
    equal=' AND '.join((
        'e.sip_timestamp_ns=r.sip_timestamp_ns',
        'isNotDistinctFrom(e.participant_timestamp_ns,r.participant_timestamp_ns)',
        'e.reporting_condition_entries=r.reporting_condition_entries','e.reporting_reason=r.reporting_reason',
        f'e.source_digest={literal(source_digest)}'))
    return f"""SELECT toDate({literal(day)}) source_date,e.ticker ticker,e.ordinal ordinal,
      r.sip_timestamp_ns sip_timestamp_ns,r.participant_timestamp_ns participant_timestamp_ns,
      r.reporting_condition_entries reporting_condition_entries,r.reporting_reason reporting_reason,
      {literal(source_digest)} source_digest,toUInt8(e.present!=1 OR r.present!=1 OR NOT ({equal})) mismatch,
      toUInt8(r.tie_count!=1) ambiguous,r.original_listing_tape original_listing_tape
      FROM (SELECT *,toUInt8(1) present,row_number() OVER (PARTITION BY ticker ORDER BY ordinal) trade_rank
        FROM {target} WHERE source_date=toDate({literal(day)}) AND cityHash64(ticker)%{total}={bucket}) e
      FULL OUTER JOIN (SELECT *,toUInt8(1) present,count() OVER (PARTITION BY ticker,sip_timestamp_us,sequence_number) tie_count,
        row_number() OVER (PARTITION BY ticker ORDER BY sip_timestamp_us,sequence_number) trade_rank FROM ({raw})) r
      USING (ticker,trade_rank) SETTINGS join_algorithm='full_sorting_merge',join_use_nulls=0"""


def publish(query,day,source_digest,clock_hash,file_hash,expected,stage,guard,*,database='q_live',emit=print):
    identifier(database);identifier(stage)
    for definition in tables():
        guard();query(definition.ddl(database),read=False)
        columns=query(f'SELECT name,type,compression_codec FROM system.columns WHERE database={literal(database)} '
            f'AND table={literal(definition.name)} ORDER BY position')
        actual=[(r['name'],r['type'].replace(' ',''),r['compression_codec'].replace(' ','')) for r in columns]
        wanted_columns=[(c.name,c.type.replace(' ',''),'CODEC('+c.codec.replace(' ','')+')') for c in definition.columns]
        if actual!=wanted_columns:raise ValueError('Original tape schema or codecs differ')
    schema.verify_auxiliary_storage(query,database,(TABLE,RECEIPTS))
    where=f'source_date=toDate({literal(day)})'
    old=query(f'SELECT * FROM {database}.{RECEIPTS} WHERE {where}')
    if len(old)>1:raise ValueError('Original tape receipt is duplicated')
    if old:
        row=old[0]
        wanted=dict(source_date=str(day),source_digest=source_digest,clock_evidence_hash=clock_hash,
            file_digest=file_hash,tape_evidence_hash=row['tape_evidence_hash'],trade_rows=expected,version=VERSION)
        if row!=wanted or native._hash(query,fingerprint_sql(database,day))!=row['tape_evidence_hash']:
            raise ValueError('Frozen original tape evidence differs')
        return row
    for bucket in range(16):
        guard();bounded=where+f' AND cityHash64(ticker)%16={bucket}'
        query(f'INSERT INTO {database}.{TABLE} ({FIELDS}) SELECT '+','.join('s.'+n for n in FIELDS.split(','))+
            f' FROM (SELECT {FIELDS} FROM {database}.{stage} WHERE {bounded}) s LEFT ANTI JOIN '
            f'(SELECT source_date,ticker,ordinal FROM {database}.{TABLE} WHERE {bounded}) p USING(source_date,ticker,ordinal)',read=False)
        for left,right in ((stage,TABLE),(TABLE,stage)):
            guard()
            if int(query(f'SELECT count() n FROM (SELECT {FIELDS} FROM {database}.{left} WHERE {bounded} '
                    f'EXCEPT ALL SELECT {FIELDS} FROM {database}.{right} WHERE {bounded})')[0]['n']):
                raise ValueError('Original tape multiset differs from reconciled acquisition')
        emit(f'Original tape {day} | publication={bucket+1}/16')
    audit=query(f'SELECT sum(n) n,sum(keys) keys FROM (SELECT ticker,count() n,uniqExact(ordinal) keys '
        f'FROM {database}.{TABLE} WHERE {where} GROUP BY ticker) SETTINGS optimize_aggregation_in_order=1')[0]
    if int(audit['n'])!=expected or int(audit['keys'])!=expected:
        raise ValueError('Original tape does not cover every canonical trade')
    guard()
    wanted=dict(source_date=str(day),source_digest=source_digest,clock_evidence_hash=clock_hash,
        file_digest=file_hash,tape_evidence_hash=native._hash(query,fingerprint_sql(database,day)),trade_rows=expected,version=VERSION)
    insert_rows(query,database,RECEIPTS,[wanted])
    if query(f'SELECT * FROM {database}.{RECEIPTS} WHERE {where}')!=[wanted]:
        raise ValueError('Original tape receipt publication is uncertain')
    return wanted
