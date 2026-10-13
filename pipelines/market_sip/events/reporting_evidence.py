"""Compact original-clock evidence preserved by new-day canonical ingestion."""
from pipelines.certified_research.contracts import identifier,literal

TABLE='historical_trade_reporting_ns_v1'
VERSION='original-reporting-nanosecond-evidence-v1'


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


def reconciled_projection(raw_sql,target,source_date,source_digest,fields,*,first_sip_us,last_sip_us):
    """Rank matches use full canonical-field equality; unmatched rows block sealing.

    The updater supplies its own acquisition-owned raw projection. Historical
    callers must supply an ingestion-owned ClickHouse evidence source instead.
    This function never opens a file or invents a clock from SIP time.
    """
    if len(source_digest)!=64 or any(c not in '0123456789abcdef' for c in source_digest):raise ValueError('Source digest is missing')
    database,table=target.split('.');identifier(database);identifier(table)
    if type(first_sip_us) is not int or type(last_sip_us) is not int or not 0<first_sip_us<=last_sip_us:
        raise ValueError('Certified source SIP bounds are required')
    for field in fields:identifier(field)
    columns=['event_meta',*fields]
    equality='tuple('+','.join('e.'+f for f in columns)+')=tuple('+','.join('r.'+f for f in columns)+')'
    return f"""SELECT toDate({literal(source_date)}) source_date,e.ticker ticker,e.ordinal ordinal,
      r.sip_timestamp_ns sip_timestamp_ns,r.participant_timestamp_ns participant_timestamp_ns,
      r.reporting_condition_entries reporting_condition_entries,r.reporting_reason reporting_reason,
      {literal(source_digest)} source_digest,
      toUInt8(e.present!=1 OR r.present!=1 OR NOT ({equality})) mismatch,
      toUInt8(r.tie_count!=1) ambiguous
    FROM (SELECT *,toUInt8(1) present,row_number() OVER (PARTITION BY ticker ORDER BY ordinal) trade_rank
      FROM {target} WHERE sip_timestamp_us BETWEEN {first_sip_us} AND {last_sip_us} AND bitAnd(event_meta,1)=1) e
    FULL OUTER JOIN (SELECT *,toUInt8(1) present,
      count() OVER (PARTITION BY ticker,sip_timestamp_us,sequence_number) tie_count,
      row_number() OVER (PARTITION BY ticker ORDER BY sip_timestamp_us,sequence_number) trade_rank
      FROM ({raw_sql})) r USING (ticker,trade_rank)
    SETTINGS join_algorithm='full_sorting_merge',join_use_nulls=0"""
