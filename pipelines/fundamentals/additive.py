"""Bounded parent verification for explicitly registered additive HTML facts."""
import json
from pipelines.certified_research.contracts import digest,literal,identifier
from pipelines.certified_research import sec_taxonomy


def identities(groups):
    rows=[]
    for g in groups:
        taxonomy,tag=sec_taxonomy.identity(g['concept'],allow_extension=True)
        context=dict(period_start=g['period_start'],period_end=g['period_end'],taxonomy=taxonomy,
            dimensions=json.dumps(g['dimensions'],separators=(',',':')),unit=g['unit'])
        rows.append(dict(metric=taxonomy+':'+tag,context_id=digest(context),text_value=g['value']))
    if len({(r['metric'],r['context_id']) for r in rows})!=len(rows):
        raise ValueError('Additive scope has competing semantic keys')
    return sorted(rows,key=lambda r:(r['metric'],r['context_id']))


def snapshot(query,database,cik,accession,parent):
    identifier(database)
    headers=query(f"SELECT source_id,toString(available_at) available_at,evidence_ref,recipe_hash "
        f"FROM {database}.reference_source_v1 FINAL WHERE family='sec_original_qualified' "
        f"AND entity_id={literal(cik)} AND event_key={literal(accession)} AND source_id={literal(parent)}")
    receipts=query(f'SELECT batch_key,fact_rows,output_hash,recipe_hash,population_hash '
        f'FROM {database}.reference_batch_receipt_v1 FINAL WHERE source_signature={literal(parent)} ORDER BY batch_key')
    rows=query(f'SELECT metric,context_id,text_value FROM {database}.reference_fact_v1 FINAL '
        f"WHERE entity_kind='issuer_cik' AND entity_id={literal(cik)} AND source_id={literal(parent)} "
        'ORDER BY metric,context_id LIMIT 10001')
    return dict(headers=headers,receipts=receipts,facts_hash=digest(rows)),rows


def parent_state(query,database,cik,accession,document,parent,base_groups,new_groups):
    state,rows=snapshot(query,database,cik,accession,parent)
    headers,receipts=state['headers'],state['receipts']
    if (len(headers)!=1 or not headers[0]['available_at']
            or json.loads(headers[0]['evidence_ref']).get('document')!=document):
        raise ValueError('Additive scope parent document is absent or competing')
    expected=identities(base_groups);added=identities(new_groups)
    if not receipts or any(int(r['fact_rows'])!=len(expected) or not r['output_hash'] for r in receipts):
        raise ValueError('Additive scope parent receipt differs')
    if rows!=expected:raise ValueError('Additive scope parent facts differ from reviewed original')
    if {(r['metric'],r['context_id']) for r in expected}&{(r['metric'],r['context_id']) for r in added}:
        raise ValueError('Additive scope repeats a parent fact')
    return state
