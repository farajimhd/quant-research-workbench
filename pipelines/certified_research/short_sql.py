"""Vectorized short-data normalization with explicit public-clock admission.

CNMS is the sole consolidated daily-volume scope. Component files cannot be
summed into it. No trade/settlement/ingestion-time availability fallback exists.
The caller must certify source coverage and the original publication evidence.
"""
from __future__ import annotations

from datetime import date
from uuid import UUID

from .contracts import identifier,literal
from .facts import catalog,UNITS


def clock_inventory(query,start,end):
    """Partition-bounded prerequisite counts; not a source/coverage certificate."""
    if type(start) is not date or type(end) is not date or start>end:
        raise ValueError('Invalid short clock inspection range')
    result={}
    for family,measurement in (('short_volume','trade_date'),('short_interest','settlement_date')):
        unknown=('published_at_utc IS NULL' if family=='short_volume'
                 else 'published_at_utc IS NULL AND publication_date IS NULL')
        extra=('' if family=='short_volume' else
               ',countIf(published_at_utc IS NULL AND publication_date IS NOT NULL) conservative_dates')
        rows=query(f'SELECT count() rows,countIf({unknown}) unknown_public_time,'
            f'countIf(published_at_utc IS NOT NULL) exact_times{extra} '
            f'FROM q_live.market_{family}_v1 FINAL PREWHERE {measurement} BETWEEN '
            f'toDate({literal(start)}) AND toDate({literal(end)})')
        if len(rows)!=1:raise ValueError('Short clock inventory is ambiguous')
        values={k:int(v) for k,v in rows[0].items()}
        result[family]=dict(**values,repair_required=bool(values['unknown_public_time']))
    return dict(inspection_only=True,start=str(start),end=str(end),families=result)


def short_source(database,dataset_id,entity_key,start,end,family,*,source_database='q_live'):
    identifier(database);identifier(source_database);UUID(dataset_id)
    if source_database!='q_live' and not source_database.startswith('certified_research_test_'):
        raise ValueError('Only canonical or isolated fixture reference sources are allowed')
    if (type(entity_key) is not int or not 0<entity_key<2**64 or type(start) is not date
            or type(end) is not date or start>end or family not in ('short_volume','short_interest')):
        raise ValueError('Invalid short reference scope')
    measurement='trade_date' if family=='short_volume' else 'settlement_date'
    table='market_'+family+'_v1'
    # Restrict the symbol scan first, then verify membership at measurement time.
    # The date comparison is deliberately half-open, like the identity contract.
    return f"""WITH membership AS (
      SELECT ticker,listing_key,valid_from,valid_to FROM {database}.research_membership_v1
      WHERE dataset_id=toUUID({literal(dataset_id)}) AND security_key={entity_key} AND eligible=1
    ), source AS (
      SELECT s.* FROM {source_database}.{table} s FINAL
      PREWHERE upper(s.provider_ticker) IN (SELECT ticker FROM membership)
      WHERE {measurement} BETWEEN toDate({literal(start)}) AND toDate({literal(end)})
        {"AND source_system='finra' AND source_venue='CNMS'" if family=='short_volume' else ""}
    ), mapped AS (
      SELECT s.*,m.listing_key mapped_listing_key FROM source s INNER JOIN membership m
        ON upper(s.provider_ticker)=m.ticker
      WHERE toDateTime64(s.{measurement},6,'America/New_York')>=m.valid_from
        AND toDateTime64(s.{measurement},6,'America/New_York')<m.valid_to
    ) """


def short_audit(database,dataset_id,entity_key,start,end,family,*,source_database='q_live'):
    prefix=short_source(database,dataset_id,entity_key,start,end,family,source_database=source_database)
    time=("published_at_utc IS NULL" if family=='short_volume'
          else "published_at_utc IS NULL AND publication_date IS NULL")
    event='short_volume_id' if family=='short_volume' else 'short_interest_id'
    measurement='trade_date' if family=='short_volume' else 'settlement_date'
    # Hash source values, clock evidence and identity binding, excluding ingestion clocks.
    values=('short_volume,total_volume,exempt_volume,non_exempt_volume' if family=='short_volume'
            else 'short_interest,avg_daily_volume,days_to_cover,publication_date')
    date_invalid=" OR (publication_date IS NOT NULL AND publication_date<settlement_date)" if family=='short_interest' else ''
    return prefix+f"""SELECT count() n,uniqExact({event}) unique_events,
      countIf({time}) unknown_public_time,
      countIf((published_at_utc IS NOT NULL AND toDate(published_at_utc,'America/New_York')<{measurement}){date_invalid}) invalid_public_time,
      countIf(NOT match(source_content_sha256,'^[0-9a-f]{{64}}$') OR empty(source_evidence_ref)) missing_evidence,
      lower(hex(SHA256(toJSONString(arraySort(groupArray(tuple({event},provider_ticker,{measurement},
        {values},published_at_utc,source_system,source_venue,security_id,listing_id,
        source_content_sha256,source_evidence_ref,mapped_listing_key))))))) source_hash
      FROM mapped"""


def short_projection(database,dataset_id,attempt_id,entity_key,start,end,family,*,source_database='q_live',wide=False):
    UUID(attempt_id)
    prefix=short_source(database,dataset_id,entity_key,start,end,family,source_database=source_database)
    metrics={m.name:m for m in catalog()}
    if family=='short_volume':
        measurement='trade_date';event='short_volume_id'
        values=(('short.volume','short_volume'),('short.total_volume','total_volume'),
                ('short.exempt_volume','exempt_volume'),('short.non_exempt_volume','non_exempt_volume'),
                ('short.volume_ratio','if(total_volume>0,short_volume/toFloat64(total_volume),NULL)'))
        public="toDateTime64(published_at_utc,6,'UTC')";precision=1
        invalid=('short_volume IS NULL OR total_volume IS NULL OR short_volume>total_volume '
                 'OR exempt_volume IS NULL OR non_exempt_volume IS NULL OR exempt_volume>short_volume '
                 'OR non_exempt_volume!=short_volume-exempt_volume')
        raw_values='tuple(short_volume,total_volume,exempt_volume,non_exempt_volume)'
        integer_range='arrayExists(x -> ifNull(x>9007199254740992,0),[short_volume,total_volume,exempt_volume,non_exempt_volume])'
    else:
        measurement='settlement_date';event='short_interest_id'
        values=(('short.interest','short_interest'),('short.provider_adv','avg_daily_volume'),
                ('short.provider_days_to_cover','days_to_cover'))
        public=("if(published_at_utc IS NOT NULL,toDateTime64(published_at_utc,6,'UTC'),"
                "toDateTime64(publication_date+1,6,'America/New_York'))")
        precision="if(published_at_utc IS NOT NULL,1,2)"
        invalid='short_interest IS NULL OR (days_to_cover IS NOT NULL AND (NOT isFinite(days_to_cover) OR days_to_cover<0))'
        raw_values='tuple(short_interest,avg_daily_volume,days_to_cover,publication_date)'
        integer_range='arrayExists(x -> ifNull(x>9007199254740992,0),[short_interest,avg_daily_volume])'
    expansion=','.join(f'(toUInt32({metrics[name].key}),toUInt16({UNITS[metrics[name].unit]}),'
                       f'toNullable(toFloat64({expression})))' for name,expression in values)
    prefix+=f""", normalized AS (
      SELECT *,{public} public_at,toUInt8({precision}) public_precision,
        toUInt8({integer_range}) integer_range_invalid,toUInt8(({invalid}) OR integer_range_invalid) invalid_values,
        toJSONString(tuple({event},provider_ticker,{measurement},source_system,source_venue,
          mapped_listing_key,{raw_values},
          published_at_utc,source_content_sha256,source_evidence_ref,{expansion})) source_proof,
        lower(hex(SHA256(source_proof))) evidence_hash,arrayJoin([{expansion}]) metric
      FROM mapped
    ) """
    extras=",source_system,concat('"+family+"/',"+event+" ) source_record,source_content_sha256 source_hash,source_proof proof" if wide else ''
    return prefix+f"""SELECT toUUID({literal(dataset_id)}) dataset_id,toUUID({literal(attempt_id)}) attempt_id,
      toUInt8(2) entity_kind,toUInt64({entity_key}) entity_key,metric.1 metric_key,
      toNullable({measurement}) period_start,toNullable({measurement}) period_end,toUInt8(1) context_kind,
      toUInt16(0) fiscal_year,'' fiscal_period,toUInt8(0) taxonomy_key,
      toDateTime64(public_at,6,'UTC') available_at,toUInt32(0) revision,
      if(invalid_values OR metric.3 IS NULL,CAST(NULL AS Nullable(Float64)),metric.3) value,
      metric.2 unit_key,metric.2 basis_unit_key,toUInt8(if(invalid_values,3,if(metric.3 IS NULL,2,1))) status,
      public_precision precision,toUInt8(if(public_precision=1,90,70)) confidence,
      multiIf(integer_range_invalid,'source_integer_exceeds_exact_fact_range',invalid_values,'invalid_short_source_values',
        {"metric.1="+str(metrics['short.volume_ratio'].key)+" AND total_volume=0,'zero_source_denominator'," if family=='short_volume' else ''}
        metric.3 IS NULL,'missing_source_metric','') reason,
      evidence_hash{extras} FROM normalized
      WHERE toDate(public_at,'UTC') BETWEEN toDate({literal(start)}) AND toDate({literal(end)})"""
