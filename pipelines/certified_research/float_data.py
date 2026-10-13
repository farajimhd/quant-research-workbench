"""Certified float/supply projection; all quantitative work remains in CH.

Provider observations and issuer estimates are distinct. Class allocation,
executed splits and measurement-date prices require independently certified
parents. Announcement dates never establish that a split has executed.
"""
from __future__ import annotations

from datetime import date,timedelta
from bisect import bisect_left,bisect_right
from uuid import UUID

from .contracts import identifier,literal,digest
from .facts import Metric
from .fundamentals import metric
from . import schema
from .reference_publication import ReferencePublisher

VERSION='public-float-supply-v1'
FAMILIES=('fundamentals','regular_prices','corporate_actions','float_allocation')
STAGING='research_stage_float_context_v1'


def staging_table():
    observation="Tuple(Nullable(Float64),UInt8,UInt8,UInt8,FixedString(64),Nullable(Date),Nullable(Date))"
    allocation="Tuple(Nullable(Float64),UInt8,UInt8,UInt8,FixedString(64))"
    cols=(schema.c('dataset_id','UUID'),schema.c('attempt_id','UUID'),schema.c('security_key','UInt64','T64,ZSTD(1)'),
        schema.c('cutoff',"DateTime64(6,'UTC')",'Delta,ZSTD(1)'),schema.c('cutoff_precision','UInt8'),
        schema.c('observations','Map(UInt32,'+observation+')'),schema.c('allocation',allocation))
    cols+=tuple(schema.c(n,observation) for n in ('public_float','issuer_shares','reported_float','reported_shares'))
    cols+=(schema.c('allocated','UInt8'),)
    return schema.Table(STAGING,cols,('dataset_id','security_key','attempt_id','cutoff'),'toYYYYMM(cutoff)')


def key(name):return Metric(name,'shares').key


def parent_hash(receipts):
    if not set(FAMILIES)<=set(receipts) or set(receipts)-set((*FAMILIES,'reported_supply')):
        raise ValueError('Float requires reported facts, prices, corporate coverage and allocation parents')
    return digest(dict(version=VERSION,parents={k:v['output_hash'] for k,v in sorted(receipts.items())},
        policy=dict(sec_age_days=520,price_gap_days=7,provider_first=True,estimate_confidence=40)))


def required_price_sessions(query,database,claim,fundamental_scope,start,end,*,maximum_measurements=2048):
    """Derive measurement-price context from certified SEC public-float facts.

    This is a conservative union across potentially usable vintages. It does
    not infer class allocation or create prices before a security was listed.
    Callers must join the result to dated membership and canonical coverage.
    """
    from .source import exchange_sessions
    identifier(database)
    if (type(start) is not date or type(end) is not date or start>end
            or fundamental_scope.entity_kind!=1 or fundamental_scope.family!='fundamentals'
            or fundamental_scope.start>start or fundamental_scope.end<end
            or type(maximum_measurements) is not int or not 1<=maximum_measurements<=10000):
        raise ValueError('Invalid certified float-price context request')
    publisher=ReferencePublisher(query,database,claim);receipt=publisher.receipt(fundamental_scope)
    if receipt is None:raise ValueError('Float measurement context requires certified fundamentals')
    rows=query(f'SELECT DISTINCT period_end FROM {database}.research_facts_v1 WHERE '
        +fundamental_scope.rows_where(receipt['attempt_id'])+f' AND metric_key={metric("SEC public float value")} '
        f"AND context_kind=1 AND status=1 AND unit_key=1 AND basis_unit_key=1 AND period_end IS NOT NULL "
        # UTC range start may still be the prior New York date, where age is evaluated.
        f'AND period_end BETWEEN toDate({literal(start-timedelta(days=521))}) AND toDate({literal(end)}) '
        f"AND available_at<toDateTime64({literal(end+timedelta(days=1))},6,'UTC') "
        f'ORDER BY period_end LIMIT {maximum_measurements+1}')
    if len(rows)>maximum_measurements:raise ValueError('Float measurement context exceeds its metadata bound')
    measurements=tuple(date.fromisoformat(str(r['period_end'])) for r in rows)
    if tuple(sorted(set(measurements)))!=measurements:raise ValueError('Float measurement context is duplicated or unordered')
    days=exchange_sessions(measurements[0]-timedelta(days=7),measurements[-1]) if measurements else ()
    needed=set()
    for day in measurements:needed.update(days[bisect_left(days,day-timedelta(days=7)):bisect_right(days,day)])
    if publisher.receipt(fundamental_scope)!=receipt:raise ValueError('Fundamental authority changed during price-context discovery')
    result=dict(measurements=measurements,sessions=tuple(sorted(needed)),fundamentals_hash=receipt['output_hash'],
        price_gap_days=7,sec_age_days=520)
    result['context_hash']=digest(result)
    return result


def source_price_sessions(query,claim,source_guard,entity_key,cik,source_start,source_end,start,end,
                          expected_source_hash,*,maximum_measurements=2048,measurement_end=None):
    """Bootstrap context from frozen SEC inputs without publishing fundamentals.

    Uses the publication projection and rechecks the full source fingerprint.
    This proves consistency with that input, not SEC acquisition completeness.
    """
    from . import reference_sql
    from .source import exchange_sessions
    if (any(type(d) is not date for d in (source_start,source_end,start,end))
            or source_start>start or source_end<end or start>end
            or type(maximum_measurements) is not int or not 1<=maximum_measurements<=10000):
        raise ValueError('Invalid SEC float-price context request')
    measurement_end=end if measurement_end is None else measurement_end
    if type(measurement_end) is not date or not start<=measurement_end<=end:
        raise ValueError('Invalid float measurement cutoff')
    def guard():claim.check();source_guard()
    guard()
    before=reference_sql.sec_source_evidence(query,entity_key,cik,source_start,source_end)
    if before['source_hash']!=expected_source_hash:raise ValueError('Frozen SEC float-context source changed')
    namespace='00000000-0000-0000-0000-000000000001'
    projected=reference_sql.sec_projection(namespace,namespace,entity_key,cik,source_start,source_end)
    rows=query(f'SELECT DISTINCT period_end FROM ({projected}) WHERE '
        f'metric_key={metric("SEC public float value")} AND context_kind=1 AND status=1 '
        f'AND unit_key=1 AND basis_unit_key=1 AND period_end IS NOT NULL '
        f'AND period_end BETWEEN toDate({literal(start-timedelta(days=521))}) AND toDate({literal(measurement_end)}) '
        f"AND available_at<toDateTime64({literal(end+timedelta(days=1))},6,'UTC') "
        f'ORDER BY period_end LIMIT {maximum_measurements+1}')
    if len(rows)>maximum_measurements:raise ValueError('SEC float-price context exceeds its metadata bound')
    measurements=tuple(date.fromisoformat(str(r['period_end'])) for r in rows)
    if tuple(sorted(set(measurements)))!=measurements:raise ValueError('SEC float measurements are duplicated or unordered')
    days=exchange_sessions(measurements[0]-timedelta(days=7),measurements[-1]) if measurements else ()
    needed=set()
    for day in measurements:needed.update(days[bisect_left(days,day-timedelta(days=7)):bisect_right(days,day)])
    guard()
    after=reference_sql.sec_source_evidence(query,entity_key,cik,source_start,source_end)
    if after!=before:raise ValueError('SEC authority changed during float-price discovery')
    guard()
    result=dict(measurements=measurements,sessions=tuple(sorted(needed)),sec_source_hash=before['source_hash'],
        price_gap_days=7,sec_age_days=520)
    result['context_hash']=digest(result)
    return result


def projection(database,dataset_id,attempt_id,security_key,issuer_key,parents,start: date,end: date,*,wide=False,
               context_attempt=None,prepare_context=False):
    identifier(database);UUID(dataset_id);UUID(attempt_id)
    if (type(start) is not date or type(end) is not date or start>end
            or any(type(v) is not int or not 0<v<2**64 for v in (security_key,issuer_key))):
        raise ValueError('Invalid float scope')
    parent_hash(parents)
    for row in parents.values():UUID(row['attempt_id'])
    ids=','.join(f'toUUID({literal(r["attempt_id"])})' for r in parents.values())
    float_key=metric('SEC public float value');shares_key=metric('Common shares outstanding')
    provider_float=key('float.reported');provider_shares=key('shares.outstanding')
    price_key=key('market.regular_close');split_key=key('corporate.executed_split_ratio')
    announcement_key=key('corporate.announced_split_ratio');allocation_key=key('identity.float_allocation')
    # Raw selection keeps invalid latest observations; it cannot fall back to an
    # older positive value because a newer record is inconvenient or conflicting.
    prefix=f"""WITH source AS (
      SELECT * FROM {database}.research_facts_v1 WHERE dataset_id=toUUID({literal(dataset_id)})
        AND attempt_id IN ({ids}) AND ((entity_kind=1 AND entity_key={issuer_key}) OR (entity_kind=2 AND entity_key={security_key}))
    ), clock_points AS (
      SELECT clock.1 cutoff,clock.2 precision FROM (
        SELECT arrayJoin(arrayConcat([(available_at,precision)],
          if(metric_key IN ({allocation_key},{announcement_key}) AND period_end IS NOT NULL,
            [(toDateTime64(toDateTime64(ifNull(period_end,toDate(0)),6,'America/New_York'),6,'UTC'),toUInt8(1))],[]),
          if(metric_key={float_key} AND period_end IS NOT NULL,
            [(toDateTime64(toDateTime64(ifNull(period_end,toDate(0))+521,6,'America/New_York'),6,'UTC'),toUInt8(1))],[]))) clock FROM source)
    ), cutoffs AS (
      SELECT cutoff,max(precision) cutoff_precision FROM clock_points
      WHERE toDate(cutoff) BETWEEN toDate({literal(start)}) AND toDate({literal(end)}) GROUP BY cutoff
    ), latest AS (
      SELECT cutoff,metric_key,argMax(tuple(value,status,precision,confidence,evidence_hash,period_end,period_start),
        tuple(ifNull(period_end,toDate(0)),available_at,revision,evidence_hash)) observation
      FROM source CROSS JOIN cutoffs WHERE available_at<=cutoff
        AND metric_key IN ({float_key},{shares_key},{provider_float},{provider_shares})
        AND (period_end IS NULL OR period_end<=toDate(cutoff,'America/New_York'))
        AND ((entity_kind=1 AND context_kind=1 AND ((metric_key={float_key} AND unit_key=1) OR (metric_key={shares_key} AND unit_key=2)))
          OR (entity_kind=2 AND unit_key=2))
      GROUP BY cutoff,metric_key
    ), observed AS (
      SELECT cutoff,mapFromArrays(groupArray(metric_key),groupArray(observation)) observations FROM latest GROUP BY cutoff
    ), allocations AS (
      SELECT cutoff,argMax(tuple(value,status,precision,confidence,evidence_hash),tuple(period_start,available_at,revision,evidence_hash)) allocation
      FROM source CROSS JOIN cutoffs WHERE available_at<=cutoff AND entity_kind=2 AND metric_key={allocation_key}
        AND period_start<=toDate(cutoff,'America/New_York')
        AND (period_end IS NULL OR period_end>toDate(cutoff,'America/New_York')) GROUP BY cutoff
    ), candidates AS (
      SELECT c.cutoff cutoff,c.cutoff_precision cutoff_precision,o.observations observations,a.allocation allocation,
        observations[{float_key}] public_float,observations[{shares_key}] issuer_shares,
        observations[{provider_float}] reported_float,observations[{provider_shares}] reported_shares,
        (allocation.1=1 AND allocation.2=1) allocated
      FROM cutoffs c LEFT JOIN observed o USING cutoff LEFT JOIN allocations a USING cutoff
    ), prices AS (
      SELECT c.cutoff cutoff,argMax(tuple(p.value,p.status,p.precision,p.confidence,p.evidence_hash,p.period_end),
        tuple(p.period_end,p.available_at,p.revision,p.evidence_hash)) price
      FROM candidates c INNER JOIN source p ON p.entity_kind=2 AND p.metric_key={price_key}
      WHERE p.available_at<=c.cutoff AND p.unit_key=5 AND p.basis_unit_key=2
        AND p.period_end<=c.public_float.6 AND p.period_end>=c.public_float.6-7 GROUP BY cutoff
    ), corporate_latest AS (
      SELECT cutoff,metric_key,period_end,
        argMax(tuple(period_end,available_at,value,status,precision,evidence_hash),tuple(available_at,revision,evidence_hash)) action
      FROM source CROSS JOIN cutoffs WHERE available_at<=cutoff AND entity_kind=2
        AND metric_key IN ({split_key},{announcement_key}) GROUP BY cutoff,metric_key,period_end
    ), splits AS (
      SELECT c.cutoff cutoff,
        arraySort(groupArray(s.action)) actions
      FROM candidates c INNER JOIN corporate_latest s ON s.cutoff=c.cutoff AND s.metric_key={split_key}
      WHERE s.period_end<=toDate(c.cutoff,'America/New_York') GROUP BY cutoff
    ), pending AS (
      SELECT c.cutoff cutoff,count() pending_count FROM candidates c INNER JOIN corporate_latest announced
        ON announced.cutoff=c.cutoff AND announced.metric_key={announcement_key}
      LEFT JOIN corporate_latest executed ON executed.cutoff=c.cutoff AND executed.metric_key={split_key}
        AND executed.period_end=announced.period_end
      WHERE announced.period_end<=toDate(c.cutoff,'America/New_York')
        AND (executed.action.4!=1 OR executed.action.3 IS NULL)
      GROUP BY cutoff
    ), supply AS (
      SELECT c.cutoff cutoff,c.cutoff_precision cutoff_precision,c.observations observations,c.allocation allocation,
        c.public_float public_float,c.issuer_shares issuer_shares,c.reported_float reported_float,
        c.reported_shares reported_shares,c.allocated allocated,p.price price,ifNull(s.actions,[]) actions,ifNull(u.pending_count,0) pending_count,
        arrayAll(x -> x.4=1 AND x.3 IS NOT NULL AND x.3>0 AND isFinite(x.3),actions) actions_valid,
        arrayFold((factor,x) -> factor*if(x.1>public_float.6,ifNull(x.3,1.),1.),actions,1.) float_factor,
        arrayFold((factor,x) -> factor*if(x.1>issuer_shares.6,ifNull(x.3,1.),1.),actions,1.) shares_factor,
        arrayFold((factor,x) -> factor*if(x.1>reported_float.6,ifNull(x.3,1.),1.),actions,1.) reported_factor,
        arrayFold((factor,x) -> factor*if(x.1>reported_shares.6,ifNull(x.3,1.),1.),actions,1.) reported_shares_factor,
        if(reported_shares.2=1 AND reported_shares.1>0,reported_shares.1*reported_shares_factor,
          if(allocated AND issuer_shares.2=1 AND issuer_shares.1>0,issuer_shares.1*shares_factor,CAST(NULL AS Nullable(Float64)))) shares,
        if(reported_float.2=1 AND reported_float.1>0,reported_float.1*reported_factor,CAST(NULL AS Nullable(Float64))) reported,
        if(allocated AND public_float.2=1 AND public_float.1>0 AND public_float.6>=toDate(cutoff,'America/New_York')-520
          AND price.2=1 AND price.1>0,public_float.1/price.1*float_factor,CAST(NULL AS Nullable(Float64))) estimated,
        (reported IS NOT NULL AND shares IS NOT NULL AND reported>shares) reported_conflict,
        (estimated IS NOT NULL AND shares IS NOT NULL AND estimated>shares) estimated_conflict,
        multiIf(NOT actions_valid,'invalid_executed_split',pending_count>0,'unconfirmed_split_execution',
          reported_conflict,'reported_float_exceeds_outstanding_shares','') supply_failure,
        multiIf(notEmpty(supply_failure),supply_failure,NOT allocated,'unqualified_issuer_security_allocation',
          public_float.2!=1 OR public_float.1 IS NULL OR public_float.1<=0,'missing_sec_public_float',
          public_float.6<toDate(cutoff,'America/New_York')-520,'stale_sec_public_float',
          price.2!=1 OR price.1 IS NULL OR price.1<=0,'missing_aligned_certified_price',
          estimated_conflict,'estimate_exceeds_outstanding_shares','') estimate_failure
      FROM candidates c LEFT JOIN prices p USING cutoff LEFT JOIN splits s USING cutoff LEFT JOIN pending u USING cutoff
    ), expanded AS (
      SELECT *,arrayJoin([
        ({provider_float},reported,if(notEmpty(supply_failure),supply_failure,if(reported IS NULL,'missing_certified_reported_float',''))),
        ({key('float.estimated')},estimated,estimate_failure),
        ({key('float.lower_bound')},reported,if(notEmpty(supply_failure),supply_failure,if(reported IS NULL,'no_reported_lower_bound',''))),
        ({key('float.upper_bound')},if(reported IS NOT NULL,reported,shares),if(notEmpty(supply_failure),supply_failure,
          if(reported IS NULL AND shares IS NULL,'missing_qualified_share_supply',''))),
        ({provider_shares},shares,if(notEmpty(supply_failure),supply_failure,if(shares IS NULL,'missing_qualified_outstanding_shares','')))
      ]) output,
      toJSONString(tuple({literal(VERSION)},output.1,public_float,issuer_shares,reported_float,reported_shares,
        allocation,price,actions,output.2,output.3)) proof,lower(hex(SHA256(proof))) evidence_hash
      FROM supply
    ), changes AS (
      SELECT *,lagInFrame(evidence_hash,1,'') OVER (PARTITION BY output.1 ORDER BY cutoff
        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) previous_hash FROM expanded
    ) """
    if prepare_context:
        context=prefix.split('    ), prices AS (',1)[0]+') '
        fields=','.join(c.name for c in staging_table().columns if c.name not in ('dataset_id','attempt_id','security_key'))
        return context+f'SELECT toUUID({literal(dataset_id)}) dataset_id,toUUID({literal(attempt_id)}) attempt_id,'+f'toUInt64({security_key}) security_key,{fields} FROM candidates'
    if context_attempt is not None:
        UUID(context_attempt)
        source_head=prefix.split('    ), clock_points AS (',1)[0]+'), '
        tail=prefix.split('    ), prices AS (',1)[1]
        fields=','.join(c.name for c in staging_table().columns if c.name not in ('dataset_id','attempt_id','security_key'))
        prefix=source_head+f"""candidates AS (SELECT {fields} FROM {database}.{STAGING}
          WHERE dataset_id=toUUID({literal(dataset_id)}) AND attempt_id=toUUID({literal(context_attempt)}) AND security_key={security_key}),
          cutoffs AS (SELECT cutoff,cutoff_precision FROM candidates), prices AS ("""+tail
    extras=",'float_supply' source_system,concat('security/',toString(entity_key)) source_record,"+literal(VERSION)+" source_hash,proof" if wide else ''
    # source_hash is a deterministic formula/parent lineage digest, never a version label.
    if wide:extras=extras.replace(literal(VERSION)+' source_hash',literal(parent_hash(parents))+' source_hash')
    return prefix+f"""SELECT toUUID({literal(dataset_id)}) dataset_id,toUUID({literal(attempt_id)}) attempt_id,
      toUInt8(2) entity_kind,toUInt64({security_key}) entity_key,toUInt32(output.1) metric_key,
      CAST(NULL AS Nullable(Date)) period_start,
      multiIf(output.1 IN ({provider_float},{key('float.lower_bound')}),reported_float.6,
        output.1={key('float.estimated')},public_float.6,
        output.1={key('float.upper_bound')} AND reported IS NOT NULL,reported_float.6,
        reported_shares.2=1 AND reported_shares.1>0,reported_shares.6,issuer_shares.6) period_end,toUInt8(1) context_kind,
      toUInt16(0) fiscal_year,'' fiscal_period,toUInt8(0) taxonomy_key,cutoff available_at,toUInt32(0) revision,
      if(empty(output.3) AND output.2 IS NOT NULL AND isFinite(output.2),output.2,CAST(NULL AS Nullable(Float64))) value,
      toUInt16(2) unit_key,toUInt16(2) basis_unit_key,
      toUInt8(if(empty(output.3) AND output.2 IS NOT NULL AND isFinite(output.2),1,2)) status,
      toUInt8(greatest(cutoff_precision,public_float.3,issuer_shares.3,reported_float.3,reported_shares.3,allocation.3,price.3,
        arrayMax(arrayMap(x -> x.5,actions)))) precision,
      toUInt8(if(output.1={key('float.estimated')},40,if(reported IS NOT NULL,90,60))) confidence,
      if(notEmpty(output.3),output.3,if(output.2 IS NULL OR NOT isFinite(output.2),'nonfinite_supply_calculation',
        if(output.1={key('float.estimated')},'sec_market_value_implied_estimate',
          if(output.1={key('float.upper_bound')} AND reported IS NULL,'outstanding_shares_upper_bound','')))) reason,
      evidence_hash{extras} FROM changes WHERE evidence_hash!=previous_hash"""
