"""Compact original-clock evidence preserved by new-day canonical ingestion."""
from pipelines.certified_research.contracts import identifier,literal
from .trade_reporting_flags import reason_sql,flags_sql,REVISION

TABLE='historical_trade_reporting_ns_v1'
VERSION='original-reporting-nanosecond-evidence-v1'


def evidence_fingerprint_sql(evidence,day):
    database,table=evidence.split('.');identifier(database);identifier(table)
    fields='source_date,ticker,ordinal,sip_timestamp_ns,participant_timestamp_ns,reporting_condition_entries,reporting_reason,source_digest'
    sums=','.join(f'sum(reinterpretAsUInt64(substring(h,{offset},8))) h{i}' for i,offset in enumerate((1,9,17,25)))
    return (f'SELECT count() n,{sums} FROM (SELECT SHA256(toString(tuple({fields}))) h FROM {evidence} '
        f'WHERE source_date=toDate({literal(day)}))')


def evidence_hash(query,evidence,day):
    """Frozen original-clock input fingerprint retained in native receipts."""
    from pipelines.certified_research.contracts import digest
    rows=query(evidence_fingerprint_sql(evidence,day))
    if len(rows)!=1:raise ValueError('Native evidence fingerprint is ambiguous')
    return digest({k:int(v) for k,v in rows[0].items()})


def coverage_ddl(database='q_live',table='historical_trade_reporting_coverage_v1'):
    identifier(database);identifier(table)
    return f"""CREATE TABLE IF NOT EXISTS {database}.{table} (
      source_date Date CODEC(ZSTD(1)),revision String CODEC(ZSTD(1)),source_digest String CODEC(ZSTD(1)),
      status String CODEC(ZSTD(1)),raw_table String CODEC(ZSTD(1)),map_table String CODEC(ZSTD(1)),
      details String CODEC(ZSTD(1)),updated_at DateTime64(6,'UTC') CODEC(Delta(8),ZSTD(1))
    ) ENGINE=ReplacingMergeTree(updated_at) ORDER BY (source_date,revision)
    SETTINGS storage_policy='live_market_ssd',default_compression_codec='ZSTD(1)'"""


def certification_sql(target,evidence,day,source_digest,first_sip_us,last_sip_us):
    if len(source_digest)!=64 or any(c not in '0123456789abcdef' for c in source_digest):raise ValueError('Source digest is invalid')
    if type(first_sip_us) is not int or type(last_sip_us) is not int or not 0<first_sip_us<=last_sip_us:
        raise ValueError('Certified SIP bounds are invalid')
    for value in (target,evidence):
        db,name=value.split('.');identifier(db);identifier(name)
    computed=reason_sql("arrayStringConcat(reporting_condition_entries,',')",
        "ifNull(toString(participant_timestamp_ns),'')",'toString(sip_timestamp_ns)')
    expected=flags_sql('r.expected_reason')
    return f"""WITH evaluated AS (
      SELECT *,toUInt8(1) present,{computed} expected_reason FROM {evidence}
      WHERE source_date=toDate({literal(day)})
    ) SELECT count() n,uniqExact(tuple(e.ticker,e.ordinal)) unique_keys,
      countIf(e.present!=1 OR r.present!=1 OR intDiv(r.sip_timestamp_ns,1000)!=e.sip_timestamp_us
        OR r.source_digest!={literal(source_digest)} OR r.reporting_reason!=r.expected_reason
        OR bitAnd(e.event_meta,192)!={expected}) bad,
      countIf({expected}=64) timely,countIf({expected}=192) delayed,countIf({expected}=0) unknown
    FROM (SELECT *,toUInt8(1) present FROM {target}
      WHERE sip_timestamp_us BETWEEN {int(first_sip_us)} AND {int(last_sip_us)} AND bitAnd(event_meta,1)=1) e
    FULL OUTER JOIN evaluated r USING(ticker,ordinal)
    SETTINGS join_algorithm='full_sorting_merge',join_use_nulls=0"""


def certify(query,database,target,day,source_digest,first_sip_us,last_sip_us,expected_trades,*,table=TABLE,coverage='historical_trade_reporting_coverage_v1',expected_previous=None):
    """Publish native reporting coverage only after independent NS/flag checks."""
    import json
    from datetime import datetime,timezone
    from pipelines.certified_research.schema import verify_auxiliary_storage
    from pipelines.certified_research.publication import insert_rows
    identifier(database);identifier(table)
    if type(expected_trades) is not int or expected_trades<0:raise ValueError('Expected source trade count is invalid')
    verify_auxiliary_storage(query,database,(table,))
    counts={k:int(v) for k,v in query(certification_sql(target,database+'.'+table,day,source_digest,first_sip_us,last_sip_us))[0].items()}
    if counts['n']!=expected_trades or counts['unique_keys']!=expected_trades or counts['bad']:
        raise ValueError('Native reporting evidence does not match complete canonical source')
    identifier(coverage)
    query(coverage_ddl(database,coverage),read=False);verify_auxiliary_storage(query,database,(coverage,table))
    rows=query(f'SELECT source_digest,status,details FROM {database}.{coverage} FINAL '
        f'WHERE source_date=toDate({literal(day)}) AND revision={literal(REVISION)}')
    details=json.dumps(dict(counts=counts,evidence_version=VERSION,evidence_hash=evidence_hash(query,database+'.'+table,day)),sort_keys=True)
    wanted=dict(source_digest=source_digest,status='complete',details=details)
    if rows:
        if rows==[wanted]:return counts
        if (expected_previous is None or rows!=[expected_previous]
                or expected_previous.get('status')!='native_mutating'
                or expected_previous.get('source_digest')!=source_digest
                or json.loads(expected_previous['details']).get('evidence_hash')!=json.loads(details)['evidence_hash']):
            raise ValueError('Existing reporting certificate differs from native evidence')
    insert_rows(query,database,coverage,[dict(source_date=str(day),revision=REVISION,**wanted,
        raw_table=database+'.'+table,map_table=database+'.'+table,
        updated_at=datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f'))])
    if query(f'SELECT source_digest,status,details FROM {database}.{coverage} FINAL '
        f'WHERE source_date=toDate({literal(day)}) AND revision={literal(REVISION)}')!=[wanted]:
        raise ValueError('Native reporting certificate insert is uncertain')
    return counts


def ddl(database='q_live',table=TABLE,*,stage=False):
    identifier(database);identifier(table)
    extra=',mismatch UInt8 CODEC(ZSTD(1)),ambiguous UInt8 CODEC(ZSTD(1))' if stage else ''
    return f"""CREATE TABLE IF NOT EXISTS {database}.{table} (
      source_date Date CODEC(ZSTD(1)),ticker LowCardinality(String) CODEC(ZSTD(1)),
      ordinal UInt64 CODEC(Delta(8),ZSTD(1)),sip_timestamp_ns UInt64 CODEC(Delta(8),ZSTD(1)),
      participant_timestamp_ns Nullable(UInt64) CODEC(ZSTD(1)),
      reporting_condition_entries Array(String) CODEC(ZSTD(1)),reporting_reason UInt8 CODEC(ZSTD(1)),
      source_digest FixedString(64) CODEC(ZSTD(1)){extra}
    ) ENGINE=MergeTree PARTITION BY toYYYYMM(source_date) ORDER BY (ticker,source_date,ordinal)
    SETTINGS storage_policy='live_market_ssd',default_compression_codec='ZSTD(1)'"""


def reconciled_projection(raw_sql,target,source_date,source_digest,fields,*,first_sip_us,last_sip_us,repair_reporting_flags=False,ticker_bucket=None):
    """Rank matches use full canonical-field equality; unmatched rows block sealing.

    The updater supplies its own acquisition-owned raw projection. Historical
    callers must supply an ingestion-owned ClickHouse evidence source instead.
    This function never opens a file or invents a clock from SIP time.
    Historical reporting repair may compare the low six event-meta bits: the
    two reporting bits are independently recalculated and certified afterward.
    New-day ingestion retains exact metadata equality by default.
    """
    if len(source_digest)!=64 or any(c not in '0123456789abcdef' for c in source_digest):raise ValueError('Source digest is missing')
    database,table=target.split('.');identifier(database);identifier(table)
    if type(first_sip_us) is not int or type(last_sip_us) is not int or not 0<first_sip_us<=last_sip_us:
        raise ValueError('Certified source SIP bounds are required')
    if type(repair_reporting_flags) is not bool:raise ValueError('Reporting repair mode must be explicit Boolean')
    bucket_filter=''
    if ticker_bucket is not None:
        index,total=ticker_bucket
        if type(index) is not int or type(total) is not int or not 0<=index<total<=256:
            raise ValueError('Invalid reconciliation ticker bucket')
        bucket_filter=f' AND cityHash64(ticker)%{total}={index}'
    fields=tuple(fields)
    if len(fields)!=len(set(fields)) or 'event_meta' in fields:raise ValueError('Canonical comparison fields are duplicated')
    for field in fields:identifier(field)
    meta=lambda alias:f'bitAnd({alias}.event_meta,63)' if repair_reporting_flags else f'{alias}.event_meta'
    equality='tuple('+','.join([meta('e'),*('e.'+f for f in fields)])+')=tuple('+','.join([meta('r'),*('r.'+f for f in fields)])+')'
    return f"""SELECT toDate({literal(source_date)}) source_date,e.ticker ticker,e.ordinal ordinal,
      r.sip_timestamp_ns sip_timestamp_ns,r.participant_timestamp_ns participant_timestamp_ns,
      r.reporting_condition_entries reporting_condition_entries,r.reporting_reason reporting_reason,
      {literal(source_digest)} source_digest,
      toUInt8(e.present!=1 OR r.present!=1 OR NOT ({equality})) mismatch,
      toUInt8(r.tie_count!=1) ambiguous
    FROM (SELECT *,toUInt8(1) present,row_number() OVER (PARTITION BY ticker ORDER BY ordinal) trade_rank
      FROM {target} WHERE sip_timestamp_us BETWEEN {first_sip_us} AND {last_sip_us} AND bitAnd(event_meta,1)=1{bucket_filter}) e
    FULL OUTER JOIN (SELECT *,toUInt8(1) present,
      count() OVER (PARTITION BY ticker,sip_timestamp_us,sequence_number) tie_count,
      row_number() OVER (PARTITION BY ticker ORDER BY sip_timestamp_us,sequence_number) trade_rank
      FROM ({raw_sql})) r USING (ticker,trade_rank)
    SETTINGS join_algorithm='full_sorting_merge',join_use_nulls=0"""
