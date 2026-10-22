"""Bounded as-of and change-history access; never fabricate public clocks."""
from datetime import datetime
from pipelines.certified_research.contracts import identifier, literal
from .eligibility import require_eligible


def _timestamp(value):
    if not isinstance(value,datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('Timezone-aware cutoff required')
    from datetime import timezone
    return "toDateTime64("+literal(value.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f'))+",6,'UTC')"


def observations_sql(database, entity_kind, entities, metrics, cutoff, *, clock='recorded', since=None, known_at=None):
    """All context vintages as of cutoff, or changes plus a pre-window seed.

    Recorded mode exposes retained provider observations, not qualified research
    facts. Public mode additionally requires explicit certification; this raw
    normalizer does not grant that certification. Certified formula consumers use
    pipelines.certified_research.facts.lookup_sql and its existing receipt gates.
    """
    identifier(database)
    require_eligible(entity_kind,entities)
    if not 1<=len(entities)<=256 or not 1<=len(metrics)<=256:
        raise ValueError('Bound entities and metrics to 256 each')
    if entity_kind not in ('security','issuer','issuer_cik','account_security') or clock not in ('recorded','public'):
        raise ValueError('Explicit entity scope and recorded/public clock required')
    end=_timestamp(cutoff)
    if since is not None and since>=cutoff:
        raise ValueError('Context start must precede cutoff')
    ids=','.join(map(literal,sorted(set(entities))))
    wanted=','.join(map(literal,sorted(set(metrics))))
    eligible=" AND quality='certified' AND isNotNull(available_at)" if clock=='public' else " AND quality!='invalid'"
    time='s.available_at' if clock=='public' else 's.observed_at'
    knowledge=(f' AND observed_at<={end}' if clock=='recorded' else '')
    ordering=(f' AND ordering_time<={end}' if clock=='recorded' else '')
    if known_at is not None:
        known=_timestamp(known_at)
        knowledge+=f' AND observed_at<={known}'
        ordering+=f' AND ordering_time<={known}'
    columns=','.join(f'{alias}.{name} AS {name}' for alias,fields in (
        ('f','entity_kind entity_id metric value text_value context_id'),
        ('c','period_start period_end taxonomy dimensions unit'),
        ('s','family event_key source_id source_table source_hash evidence_ref quality reason observed_at available_at'))
        for name in fields.split())
    facts=f'''SELECT * FROM {database}.reference_fact_v1 FINAL
            WHERE entity_kind={literal(entity_kind)} AND entity_id IN ({ids})
              AND metric IN ({wanted}){ordering}'''
    base=f'''SELECT {columns},{time} at
        FROM ({facts}) f
        INNER JOIN (SELECT * FROM {database}.reference_source_v1 FINAL
            WHERE entity_kind={literal(entity_kind)} AND entity_id IN ({ids})
            {knowledge}{eligible}) s ON f.source_id=s.source_id
        INNER JOIN (SELECT * FROM {database}.reference_context_v1 FINAL
            WHERE context_id IN (SELECT context_id FROM ({facts}))) c ON f.context_id=c.context_id
        WHERE {time}<={end}'''
    if since is None:
        # Revisions selected within a period/unit/dimensional context, never
        # across periods. A late old-quarter amendment cannot replace a new one.
        return f'''SELECT * EXCEPT(rn),count() OVER (
            PARTITION BY entity_kind,entity_id,family,metric,context_id) > 1 conflict
            FROM (SELECT *,rank() OVER (
            PARTITION BY entity_kind,entity_id,family,metric,context_id
            ORDER BY at DESC,observed_at DESC) rn FROM ({base}))
            WHERE rn=1 ORDER BY entity_id,metric,period_end,at'''
    start=_timestamp(since)
    history=f'''WITH observations AS ({base})
        SELECT * FROM observations WHERE at>={start}
        UNION ALL SELECT * EXCEPT(rn) FROM (SELECT *,rank() OVER (
            PARTITION BY entity_kind,entity_id,family,metric,context_id
            ORDER BY at DESC,observed_at DESC) rn
            FROM observations WHERE at<{start}) WHERE rn=1'''
    return f'''SELECT *,count() OVER (PARTITION BY entity_kind,entity_id,family,
        metric,context_id,at,observed_at)>1 conflict FROM ({history})
        ORDER BY entity_id,metric,period_end,at'''
