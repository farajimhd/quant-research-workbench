"""Causal session-window short-volume pressure over certified CNMS facts."""
from datetime import timedelta,timezone
from .contracts import identifier,literal
from .facts import catalog
from .source import exchange_sessions
from .reference_publication import ReferencePublisher


def snapshot_sql(query,database,claim,parent,cutoff,windows=(20,)):
    """Warehouse projection; corrections enter only at their public clock.

    Windows count exchange sessions, not a ticker's number of observations.
    Missing/invalid dates withhold the ratio; no ingestion-time or settlement
    fallback establishes public availability.
    """
    identifier(database)
    if parent.family!='short_volume' or parent.entity_kind!=2 or cutoff.tzinfo is None:
        raise ValueError('Short pressure requires a public-time security short-volume parent')
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
      observations AS (SELECT c.1 day,c.2 ordinal,p.shorts shorts,p.totals totals,p.metrics metrics
        FROM calendar LEFT JOIN paired p ON p.period_end=c.1),
      terminal AS (SELECT maxIf(ordinal,metrics>0) last FROM observations)
    SELECT window_sessions,countIf(metrics=2 AND shorts.2=1 AND totals.2=1) observed_sessions,
      if(observed_sessions=window_sessions AND sumIf(totals.1,totals.2=1)>0,
        sumIf(shorts.1,shorts.2=1)/sumIf(totals.1,totals.2=1),CAST(NULL AS Nullable(Float64))) ratio,
      toUInt8(observed_sessions=window_sessions) context_ready,
      multiIf(max(last)=0,'no_public_short_volume',observed_sessions<window_sessions,'missing_or_invalid_measurement_sessions',
        sumIf(totals.1,totals.2=1)<=0,'zero_window_denominator','') reason
    FROM observations CROSS JOIN terminal ARRAY JOIN [{','.join(map(str,windows))}] AS window_sessions
    WHERE (last=0 AND ordinal=1) OR (last>0 AND ordinal<=last AND ordinal+window_sessions>last)
    GROUP BY window_sessions ORDER BY window_sessions"""
