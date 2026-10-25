"""Trust-specific original contexts, stored once under their filing issuer.

The query returns original dimensions and vintages. It neither copies facts to
trust CIKs nor adds trust investments to the guarantor's consolidated financials.
"""
import json
from pipelines.certified_research.contracts import literal, identifier
from pipelines.certified_research.sec_trust_context import ACCESSIONS, MEMBERS, SOURCE_CIK, subject
from .queries import _timestamp


def observations(query, ciks, cutoff, *, known_at=None, database='arte'):
    identifier(database)
    requested = set(ciks)
    if not requested or not requested <= set(MEMBERS.values()):
        raise ValueError('Unreviewed trust identity')
    accessions = ','.join(map(literal, (*ACCESSIONS,'0000827052-19-000034')))
    known = '' if known_at is None else ' AND observed_at<=' + _timestamp(known_at)
    sources = f"""SELECT * FROM {database}.reference_source_v1 FINAL
        WHERE entity_kind='issuer_cik' AND entity_id={literal(SOURCE_CIK)}
        AND family='sec_original_qualified' AND quality='certified'
        AND event_key IN ({accessions}) AND available_at<={_timestamp(cutoff)}{known}
        AND source_id IN (SELECT source_signature FROM {database}.reference_batch_receipt_v1 FINAL)"""
    facts = f"""SELECT * FROM {database}.reference_fact_v1 FINAL
        WHERE entity_kind='issuer_cik' AND entity_id={literal(SOURCE_CIK)}
        AND source_id IN (SELECT source_id FROM ({sources}))"""
    rows = query(f"""SELECT f.entity_id source_cik,f.source_id,f.metric,
        c.period_start,c.period_end,c.unit,c.dimensions,
        coalesce(f.text_value,toString(f.value)) value,s.available_at,s.observed_at,
        s.source_hash source_binding_hash,s.event_key accession
        FROM ({facts}) f
        INNER JOIN ({sources}) s ON f.source_id=s.source_id
        INNER JOIN (SELECT * FROM {database}.reference_context_v1 FINAL
          WHERE context_id IN (SELECT context_id FROM ({facts}))) c ON f.context_id=c.context_id
        ORDER BY s.available_at,f.source_id,f.metric,c.context_id LIMIT 10001""")
    if len(rows) > 10000:
        raise ValueError('Trust response exceeds bound; narrow the source window')
    from pipelines.certified_research.sec_html_reviewed_sce import SUBJECTS, CIK
    from pipelines.certified_research.sec_html_native import definition
    from pipelines.certified_research.sec_taxonomy import identity
    native_subjects={}
    for name,issuer in SUBJECTS.items():
        for label in ('Dividend income','Dividend distributions'):
            tax,tag=identity(definition(CIK,label,name)['concept'],allow_extension=True)
            native_subjects[tax+':'+tag]=issuer
    result = []
    for row in rows:
        owner = subject(dict(dimensions=json.loads(row['dimensions'])))
        if owner is None and row['accession']=='0000827052-19-000034':
            owner=native_subjects.get(row['metric'])
        if owner in requested:
            result.append(dict(row, subject_cik=owner, aggregation='none'))
    return result
