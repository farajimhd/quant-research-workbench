"""Qualified source-native financials with exact reporting scope and two clocks.

Names, currencies and entity scopes remain explicit. A native measure is never
silently promoted to a standard feature or added to another entity's accounts.
The same read path serves historical windows and newly published originals.
"""
from pipelines.certified_research.contracts import identifier, literal
from .eligibility import require_eligible
from .queries import _timestamp


def financial_sql(ciks, cutoff, *, since=None, known_at=None, database='arte'):
    identifier(database);require_eligible('issuer_cik',ciks)
    if not 1<=len(ciks)<=32:raise ValueError('Use 1..32 source-native issuer CIKs')
    end=_timestamp(cutoff)
    if since is not None and since>=cutoff:raise ValueError('History start must precede cutoff')
    ids=','.join(map(literal,sorted(set(ciks))))
    known='' if known_at is None else ' AND observed_at<='+_timestamp(known_at)
    facts=f'''SELECT * FROM {database}.reference_fact_v1 FINAL
        WHERE entity_kind='issuer_cik' AND entity_id IN ({ids}) AND startsWith(metric,'html-native|')'''
    base=f'''SELECT f.entity_id cik,f.metric metric,d.label label,d.statement_scope statement_scope,
        d.definition_hash definition_hash,f.context_id context_id,c.period_start period_start,
        c.period_end period_end,c.unit unit,c.dimensions dimensions,
        coalesce(f.text_value,toString(f.value)) value,
        s.available_at available_at,s.observed_at observed_at,s.source_id source_id,s.event_key accession,
        if(startsWith(c.unit,'source:'),'unresolved_source_symbol','explicit_source_unit') unit_basis
        FROM ({facts}) f
        INNER JOIN (SELECT * FROM {database}.reference_source_v1 FINAL
            WHERE entity_kind='issuer_cik' AND entity_id IN ({ids}) AND family='sec_original_qualified'
              AND quality='certified' AND isNotNull(available_at) AND available_at<={end}{known}
              AND source_id IN (SELECT source_signature FROM {database}.reference_batch_receipt_v1 FINAL)) s
            ON f.source_id=s.source_id AND f.entity_id=s.entity_id
        INNER JOIN (SELECT * FROM {database}.reference_context_v1 FINAL
            WHERE context_id IN (SELECT context_id FROM ({facts}))) c ON f.context_id=c.context_id
        INNER JOIN (SELECT * FROM {database}.reference_metric_definition_v1 FINAL
            WHERE issuer_cik IN ({ids})) d ON f.metric=d.metric AND f.entity_id=d.issuer_cik
        WHERE isNotNull(value) AND isNotNull(c.period_end) AND c.period_end<=toDate({end})
          AND f.metric=concat(c.taxonomy,':',arrayElement(splitByChar(':',f.metric),-1))'''
    key='cik,metric,context_id'
    if since is None:
        chosen=f'''SELECT * EXCEPT(vintage) FROM (SELECT *,dense_rank() OVER
            (PARTITION BY {key} ORDER BY available_at DESC) vintage FROM ({base})) WHERE vintage=1'''
    else:
        start=_timestamp(since)
        chosen=f'''WITH observations AS ({base}) SELECT * FROM observations WHERE available_at>={start}
            UNION ALL SELECT * EXCEPT(vintage) FROM (SELECT *,dense_rank() OVER
              (PARTITION BY {key} ORDER BY available_at DESC) vintage FROM observations
              WHERE available_at<{start}) WHERE vintage=1'''
    # Equivalent sources carry provenance arrays, not repeated model inputs.
    columns='cik,metric,label,statement_scope,definition_hash,context_id,period_start,period_end,unit,dimensions,value,available_at,unit_basis'
    grouped=f'''SELECT {columns},min(observed_at) observed_at,
        arraySort(groupUniqArray(source_id)) source_ids,arraySort(groupUniqArray(accession)) accessions
        FROM ({chosen}) GROUP BY {columns}'''
    return f'''SELECT *,uniqExact(value) OVER (PARTITION BY {key},available_at)>1 conflict
        FROM ({grouped}) ORDER BY cik,metric,period_end,period_start,available_at'''
