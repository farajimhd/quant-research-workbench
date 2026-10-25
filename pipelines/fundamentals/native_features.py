"""Read-only, exact-source feature projection shared by history and live queries."""
from pipelines.certified_research.contracts import literal, identifier, digest
from .native_feature_mappings import MAPPINGS


def mapping_hash():
    return digest(MAPPINGS)


def base_sql(ciks, database, known_at=None):
    from .queries import _timestamp
    identifier(database)
    rows=[r for r in MAPPINGS if r[0] in ciks]
    if not rows:return None
    keys=[(r[0],r[1],r[3],r[4]) for r in rows]
    if len(keys)!=len(set(keys)):raise ValueError('Competing native feature mappings')
    ids=','.join(map(literal,sorted({r[0] for r in rows})))
    sources=','.join(map(literal,sorted({r[1] for r in rows})))
    mappings=' UNION ALL '.join('SELECT '+','.join(literal(v)+' '+n for v,n in zip(
        r,('cik','source_id','document_hash','metric','unit','feature','serving_unit','currency_evidence'))) for r in rows)
    known='' if known_at is None else ' AND observed_at<='+_timestamp(known_at)
    facts=f'''SELECT * FROM {database}.reference_fact_v1 FINAL
        WHERE entity_kind='issuer_cik' AND entity_id IN ({ids}) AND source_id IN ({sources})'''
    return f'''SELECT f.entity_id cik,m.feature feature,f.metric source_metric,
        c.period_start,c.period_end,m.serving_unit unit,c.unit source_unit,c.dimensions dimensions,
        c.dimensions source_dimensions,s.source_id AS source_id,s.family,s.event_key accession,
        s.observed_at,coalesce(f.text_value,toString(f.value)) value,s.available_at public_at,
        1 authority_priority FROM ({facts}) f
        INNER JOIN ({mappings}) m ON f.entity_id=m.cik AND f.source_id=m.source_id AND f.metric=m.metric
        INNER JOIN (SELECT * FROM {database}.reference_source_v1 FINAL
            WHERE entity_kind='issuer_cik' AND entity_id IN ({ids}) AND source_id IN ({sources})
              AND family='sec_original_qualified' AND quality='certified'{known}
              AND source_id IN (SELECT source_signature FROM {database}.reference_batch_receipt_v1 FINAL
                  WHERE source_signature IN ({sources}))) s ON f.source_id=s.source_id
        INNER JOIN (SELECT * FROM {database}.reference_context_v1 FINAL
            WHERE context_id IN (SELECT context_id FROM ({facts}))) c ON f.context_id=c.context_id
        WHERE c.unit=m.unit AND c.dimensions IN ('[]','{{}}','')
          AND f.metric=concat(c.taxonomy,':',arrayElement(splitByChar(':',f.metric),-1))
          AND startsWith(c.taxonomy,'html-native|') AND isNotNull(c.period_end) AND isNotNull(value)'''
