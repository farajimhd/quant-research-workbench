"""Causal session-window short-volume pressure over certified CNMS facts."""
from datetime import timedelta,timezone
from .contracts import identifier,literal
from .facts import catalog
from .source import exchange_sessions
from .reference_publication import ReferencePublisher
from .contracts import digest

STAGING='research_stage_short_pressure_v1'


def parent_hash(receipt,windows):
    return digest(dict(short_volume=receipt['output_hash'],windows=windows,recipe='cnms-session-pressure-v1'))


def staging_table():
    from .schema import TABLES,Table
    table=next(t for t in TABLES if t.name=='research_short_pressure_v1')
    return Table(STAGING,table.columns,table.order,table.partition)


def projection_sql(database,scope,attempt,parent,receipt,windows):
    """Produce every public vintage in one warehouse query, including corrections."""
    metrics={m.name:m.key for m in catalog()};short=metrics['short.volume'];total=metrics['short.total_volume']
    sessions=exchange_sessions(parent.start-timedelta(days=max(windows)*2+14),scope.end)
    dates=','.join(f'toDate({literal(d)})' for d in sessions)
    start=f"toDateTime64({literal(str(scope.start)+' 00:00:00')},6,'UTC')"
    end=f"toDateTime64({literal(str(scope.end+timedelta(days=1))+' 00:00:00')},6,'UTC')"
    return f"""WITH source AS (SELECT * FROM {database}.research_facts_v1
      WHERE {parent.rows_where(receipt['attempt_id'])} AND metric_key IN ({short},{total})),
      clocks AS (SELECT DISTINCT available_at cutoff FROM source WHERE available_at>={start} AND available_at<{end}
        UNION DISTINCT SELECT {start} cutoff),
      calendar AS (SELECT arrayJoin(arrayZip([{dates}],arrayEnumerate([{dates}]))) c),
      latest AS (SELECT cutoff,period_end,metric_key,argMax(tuple(value,status),tuple(available_at,revision)) observation
        FROM source CROSS JOIN clocks WHERE available_at<=cutoff AND period_end<=toDate(cutoff)
        GROUP BY cutoff,period_end,metric_key),
      paired AS (SELECT cutoff,period_end,argMaxIf(observation,metric_key,metric_key={short}) shorts,
        argMaxIf(observation,metric_key,metric_key={total}) totals,count() metrics FROM latest GROUP BY cutoff,period_end),
      observations AS (SELECT k.cutoff cutoff,c.1 day,c.2 ordinal,p.shorts shorts,p.totals totals,p.metrics metrics,
        toUInt8(p.metrics=2 AND p.shorts.2=1 AND p.totals.2=1 AND p.shorts.1>=0
          AND p.totals.1>=p.shorts.1) valid FROM clocks k CROSS JOIN calendar
        LEFT JOIN paired p ON p.cutoff=k.cutoff AND p.period_end=c.1),
      terminal AS (SELECT cutoff,maxIf(ordinal,metrics>0) last FROM observations GROUP BY cutoff),
      windows AS (SELECT o.cutoff cutoff,window_sessions,
        if(max(last)>0,toNullable(maxIf(day,ordinal=last)),CAST(NULL AS Nullable(Date))) window_end,
        countIf(valid=1) observed_sessions,sumIf(shorts.1,valid=1) shorts_sum,sumIf(totals.1,valid=1) total_sum,
        max(last) last_ordinal FROM observations o INNER JOIN terminal t ON t.cutoff=o.cutoff
        ARRAY JOIN [{','.join(map(str,windows))}] AS window_sessions
        WHERE (last=0 AND ordinal=1) OR (last>0 AND ordinal<=last AND ordinal+window_sessions>last)
        GROUP BY o.cutoff,window_sessions)
      SELECT toUUID({literal(scope.dataset_id)}) dataset_id,toUUID({literal(attempt)}) attempt_id,
        toUInt8(2) entity_kind,toUInt64({scope.entity_key}) entity_key,cutoff available_at,
        toUInt16(window_sessions) window_sessions,window_end,toUInt16(observed_sessions) observed_sessions,
        if(observed_sessions=window_sessions AND total_sum>0,shorts_sum/total_sum,CAST(NULL AS Nullable(Float64))) ratio,
        toUInt8(observed_sessions=window_sessions) context_ready,
        multiIf(last_ordinal=0,'no_public_short_volume',observed_sessions<window_sessions,
          'missing_or_invalid_measurement_sessions',total_sum<=0,'zero_window_denominator','') reason FROM windows"""


def run(scope,parent,windows,query,claim,source_guard,*,database='arte'):
    """Stage once, independently fingerprint, publish, and reverify on resume."""
    from uuid import uuid4
    from datetime import datetime
    from . import schema
    from .publication import evidence_sql
    identifier(database);claim.check();source_guard()
    if (scope.family!='short_pressure' or scope.entity_kind!=2
            or (scope.dataset_id,scope.entity_key)!=(parent.dataset_id,parent.entity_key)
            or parent.start>scope.start or parent.end<scope.end or (parent.end-parent.start).days>1000):
        raise ValueError('Invalid bounded short-pressure dependency')
    # Shared snapshot validation checks windows, source units and public range.
    snapshot_sql(query,database,claim,parent,datetime.combine(scope.end,datetime.min.time(),timezone.utc),windows)
    publisher=ReferencePublisher(query,database,claim);receipt=publisher.receipt(parent)
    if scope.parent_hash!=parent_hash(receipt,windows):raise ValueError('Short-pressure dependency changed')
    schema.verify_storage(query,database,require_tables=True)
    schema.verify_auxiliary_storage(query,database,(STAGING,))
    columns=query(f'SELECT name,type,compression_codec FROM system.columns WHERE database={literal(database)} '
        f'AND table={literal(STAGING)} ORDER BY position')
    actual=[(r['name'],r['type'].replace(' ',''),r['compression_codec'].replace(' ','')) for r in columns]
    expected_columns=[(c.name,c.type.replace(' ',''),'CODEC('+c.codec.replace(' ','')+')') for c in staging_table().columns]
    if actual!=expected_columns:raise ValueError('Short-pressure staging column/codec contract differs')
    existing=publisher.receipt(scope)
    if existing:return existing
    attempt=str(uuid4());table=next(t for t in schema.TABLES if t.name=='research_short_pressure_v1')
    claim.check();source_guard()
    query(f'INSERT INTO {database}.{STAGING} '+projection_sql(database,scope,attempt,parent,receipt,windows),read=False)
    select=f'SELECT * FROM {database}.{STAGING} WHERE '+scope.rows_where(attempt)
    expected={k:int(v) for k,v in query(evidence_sql(table,select,'tuple(available_at,window_sessions)'))[0].items()}
    if expected['n']!=expected['unique_keys']:raise ValueError('Ambiguous short-pressure vintages')
    claim.check();source_guard()
    if publisher.receipt(parent)!=receipt:raise ValueError('Short-pressure source changed during calculation')
    query(f'INSERT INTO {database}.research_short_pressure_v1 '+select,read=False)
    claim.check();source_guard()
    return publisher.publish(scope,attempt,expected)


def snapshot_sql(query,database,claim,parent,cutoff,windows=(20,)):
    """Warehouse projection; corrections enter only at their public clock.

    Windows count exchange sessions, not a ticker's number of observations.
    Missing/invalid dates withhold the ratio; no ingestion-time or settlement
    fallback establishes public availability.
    """
    identifier(database)
    if parent.family!='short_volume' or parent.entity_kind!=2 or cutoff.tzinfo is None:
        raise ValueError('Short pressure requires a public-time security short-volume parent')
    if not parent.start<=cutoff.astimezone(timezone.utc).date()<=parent.end:
        raise ValueError('Short-pressure cutoff is outside its certified public-time range')
    if (not windows or tuple(sorted(set(windows)))!=windows or len(windows)>32
            or any(type(n) is not int or not 0<n<=1000 for n in windows)):
        raise ValueError('Invalid bounded session windows')
    receipt=ReferencePublisher(query,database,claim).receipt(parent)
    if receipt is None:raise ValueError('Short-pressure input is not certified')
    metrics={m.name:m.key for m in catalog()};short=metrics['short.volume'];total=metrics['short.total_volume']
    invalid=query(f'SELECT countIf(unit_key!=2 OR basis_unit_key!=2 OR taxonomy_key!=0 OR context_kind!=1 '
        f'OR period_end IS NULL OR period_start IS NULL OR period_start!=period_end) bad FROM {database}.research_facts_v1 '
        f"WHERE {parent.rows_where(receipt['attempt_id'])} AND metric_key IN ({short},{total})")[0]
    if int(invalid['bad']):raise ValueError('Short-pressure source measurement/unit context is invalid')
    sessions=exchange_sessions(parent.start-timedelta(days=max(windows)*2+14),min(parent.end,cutoff.date()))
    if not sessions:raise ValueError('Short-pressure calendar context is empty')
    dates=','.join(f'toDate({literal(d)})' for d in sessions)
    time=cutoff.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f')
    return f"""WITH calendar AS (SELECT arrayJoin(arrayZip([{dates}],arrayEnumerate([{dates}]))) c),
      latest AS (SELECT period_end,metric_key,argMax(tuple(value,status),tuple(available_at,revision)) observation
        FROM {database}.research_facts_v1 WHERE {parent.rows_where(receipt['attempt_id'])}
          AND metric_key IN ({short},{total}) AND period_end<=toDate({literal(cutoff.date())})
          AND available_at<=toDateTime64({literal(time)},6,'UTC') GROUP BY period_end,metric_key),
      paired AS (SELECT period_end,argMaxIf(observation,metric_key,metric_key={short}) shorts,
        argMaxIf(observation,metric_key,metric_key={total}) totals,count() metrics FROM latest GROUP BY period_end),
      observations AS (SELECT c.1 day,c.2 ordinal,p.shorts shorts,p.totals totals,p.metrics metrics,
        toUInt8(p.metrics=2 AND p.shorts.2=1 AND p.totals.2=1 AND p.shorts.1>=0
          AND p.totals.1>=p.shorts.1) valid
        FROM calendar LEFT JOIN paired p ON p.period_end=c.1),
      terminal AS (SELECT maxIf(ordinal,metrics>0) last FROM observations)
    SELECT window_sessions,if(max(last)>0,toNullable(maxIf(day,ordinal=last)),CAST(NULL AS Nullable(Date))) window_end,
      countIf(valid=1) observed_sessions,
      if(observed_sessions=window_sessions AND sumIf(totals.1,valid=1)>0,
        sumIf(shorts.1,valid=1)/sumIf(totals.1,valid=1),CAST(NULL AS Nullable(Float64))) ratio,
      toUInt8(observed_sessions=window_sessions) context_ready,
      multiIf(max(last)=0,'no_public_short_volume',observed_sessions<window_sessions,'missing_or_invalid_measurement_sessions',
        sumIf(totals.1,valid=1)<=0,'zero_window_denominator','') reason
    FROM observations CROSS JOIN terminal ARRAY JOIN [{','.join(map(str,windows))}] AS window_sessions
    WHERE (last=0 AND ordinal=1) OR (last>0 AND ordinal<=last AND ordinal+window_sessions>last)
    GROUP BY window_sessions ORDER BY window_sessions"""
