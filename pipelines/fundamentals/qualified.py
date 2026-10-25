"""Publish verified original facts into the existing normalized ARTE authority.

Exact decimal text is authoritative. This never converts original values to
Float64, sums subsidiaries, or certifies coverage beyond the verified document.
"""
from datetime import timezone
import json
from pathlib import Path
from . import schema
from .producer import names,_different
from .eligibility import require_eligible
from pipelines.certified_research import sec_original_authority,sec_taxonomy
from pipelines.certified_research.contracts import digest,literal,identifier
from pipelines.certified_research.publication import insert_rows


def recipe_hash():
    return digest(dict(version='arte-qualified-original-v1',
        publisher=Path(__file__).read_text(encoding='utf-8'),
        authority=Path(sec_original_authority.__file__).read_text(encoding='utf-8')))


def publish(query,source_database,database,request,binding_hash,population_hash,observed_at,claim,source_guard,*,occurrence=None):
    identifier(database);identifier(source_database)
    if observed_at.tzinfo is None:raise ValueError('Qualified publication requires an aware observation timestamp')
    require_eligible('issuer_cik',[str(request[0]).zfill(10)])
    claim.check();schema.verify(query,database)
    scope={} if occurrence is None else dict(occurrence=occurrence)
    verified=sec_original_authority.verify_document(query,source_database,*request,binding_hash,claim,source_guard,**scope)
    return _publish_verified(query,source_database,database,request,binding_hash,population_hash,
                             observed_at,claim,source_guard,verified)


def _publish_verified(query,source_database,database,request,binding_hash,population_hash,
                      observed_at,claim,source_guard,verified):
    """Internal normalized publisher shared by independently verified authorities."""
    recipe=recipe_hash();source_id=verified['authority_hash']
    stamp=observed_at.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f')
    prior=query(f"SELECT DISTINCT toString(observed_at) observed_at,recipe_hash FROM {database}.reference_source_v1 FINAL "
        f"WHERE family='sec_original_qualified' AND entity_id={literal(verified['cik'])} AND source_id={literal(source_id)}")
    if len(prior)>1:raise ValueError('Qualified original has competing observation timestamps')
    if prior:
        # Observation time belongs to the authority, not each campaign that
        # encounters it. Preserve it across profile/full-run and resume paths.
        from datetime import datetime
        stamp=datetime.fromisoformat(prior[0]['observed_at']).strftime('%Y-%m-%d %H:%M:%S.%f')
        recipe=prior[0]['recipe_hash']
    key=digest(dict(source_id=source_id,recipe=recipe,population_hash=population_hash))
    stage=f'{database}.reference_stage_v1 FINAL WHERE batch_key={literal(key)}'
    receipt_where=f'batch_key={literal(key)} AND source_signature={literal(source_id)} AND recipe_hash={literal(recipe)} AND population_hash={literal(population_hash)}'
    source=dict(source_id=source_id,family='sec_original_qualified',entity_kind='issuer_cik',entity_id=verified['cik'],
        event_key=request[1],source_table=verified.get('source_table',source_database+'.research_sec_reported_document_v1'),
        source_hash=binding_hash,evidence_ref=json.dumps(dict(authority_hash=source_id,binding_hash=binding_hash,
            document=request[2],scope=verified.get('qualification_scope','document'),whole_scope_certified=False,
            **({'time_evidence':verified['time_evidence']} if 'time_evidence' in verified else {})),sort_keys=True,separators=(',',':')),
        available_at=verified['available_at'],observed_at=stamp,quality='certified',
        reason=({'reviewed_html_observations':'qualified_reviewed_html_observations',
            'all_tagged_numeric':'qualified_all_tagged_numeric'}.get(verified.get('qualification_scope'),
            'qualified_original_document_scope')),recipe_hash=recipe)
    rows=[]
    for group in verified['groups']:
        taxonomy,tag=sec_taxonomy.identity(group['concept'],allow_extension=True)
        context=dict(period_start=group['period_start'],period_end=group['period_end'],taxonomy=taxonomy,
            dimensions=json.dumps(group['dimensions'],separators=(',',':')),unit=group['unit'])
        context=dict(context_id=digest(context),**context)
        rows.append(dict(batch_key=key,**source,**context,metric=taxonomy+':'+tag,value=None,text_value=group['value']))
    if not rows or len(rows)>10000:raise ValueError('Qualified original publication requires bounded nonempty groups')
    identities={(r['metric'],r['context_id']) for r in rows}
    if len(identities)!=len(rows):raise ValueError('Competing qualified original semantic groups')
    expected_hash=digest(rows)
    definitions=[]
    native_concepts={g['concept'] for g in verified['groups']
        if sec_taxonomy.identity(g['concept'],allow_extension=True)[0].startswith('html-native|')}
    if native_concepts!=set(verified.get('native_definitions',{})):
        raise ValueError('Native facts and qualified definitions differ')
    for concept,definition in verified.get('native_definitions',{}).items():
        from pipelines.certified_research.sec_html_native import definition as native_definition
        if (native_definition(verified['cik'],definition['label'],definition['statement_scope'])!=definition
                or concept!=definition['concept']):
            raise ValueError('Native definition differs from qualified issuer/source')
        taxonomy,tag=sec_taxonomy.identity(concept,allow_extension=True)
        definitions.append(dict(metric=taxonomy+':'+tag,**{k:definition[k] for k in
            ('issuer_cik','label','statement_scope','definition_hash')}))
    if definitions:
        wanted=','.join(literal(d['metric']) for d in definitions)
        sql=f'SELECT {names(schema.METRIC)} FROM {database}.reference_metric_definition_v1 FINAL WHERE metric IN ({wanted})'
        prior_definitions=query(sql)
        expected={d['metric']:d for d in definitions}
        if any(expected.get(d['metric'])!=d for d in prior_definitions):
            raise ValueError('Native metric definition conflicts with retained metadata')
        present={d['metric'] for d in prior_definitions}
        missing=[d for d in definitions if d['metric'] not in present]
        for start in range(0,len(missing),256):
            source_guard();claim.check()
            insert_rows(query,database,'reference_metric_definition_v1',missing[start:start+256],maximum_bytes=2*1024**2)
        if sorted(query(sql),key=lambda d:d['metric'])!=sorted(definitions,key=lambda d:d['metric']):
            raise ValueError('Native metric definition readback differs')
    receipts=query(f'SELECT fact_rows,output_hash FROM {database}.reference_batch_receipt_v1 FINAL WHERE {receipt_where}')
    if receipts:
        if len(receipts)!=1 or int(receipts[0]['fact_rows'])!=len(rows) or receipts[0]['output_hash']!=expected_hash:
            raise ValueError('Qualified receipt differs from verified originals')
        # Completed receipts are immutable. Explicit audit handles external
        # output damage; normal resume avoids writing prior completed work.
        return dict(state='reused',rows=len(rows),batch_key=key,output_hash=expected_hash,source_id=source_id)
    claim.check();source_guard()
    query(f'ALTER TABLE {database}.reference_stage_v1 DROP PARTITION {literal(key)}',read=False)
    for start in range(0,len(rows),256):
        claim.check();insert_rows(query,database,'reference_stage_v1',rows[start:start+256],maximum_bytes=2*1024**2)
    contexts=f'SELECT DISTINCT {names(schema.CONTEXT)} FROM {stage}'
    facts=f'SELECT DISTINCT entity_kind,entity_id,metric,observed_at ordering_time,source_id,context_id,value,text_value FROM {stage}'
    headers=f'SELECT DISTINCT {names(schema.SOURCE)} FROM {stage}'
    source_guard();claim.check()
    query(f'INSERT INTO {database}.reference_context_v1 ({names(schema.CONTEXT)}) {contexts}',read=False)
    query(f'INSERT INTO {database}.reference_fact_v1 ({names(schema.FACT)}) {facts}',read=False)
    _different(query,contexts,f'SELECT {names(schema.CONTEXT)} FROM {database}.reference_context_v1 FINAL WHERE context_id IN (SELECT context_id FROM {stage})')
    _different(query,facts,f'SELECT {names(schema.FACT)} FROM {database}.reference_fact_v1 FINAL WHERE entity_kind=\'issuer_cik\' AND entity_id={literal(verified["cik"])} AND source_id={literal(source_id)}')
    source_guard();claim.check()
    query(f'INSERT INTO {database}.reference_source_v1 ({names(schema.SOURCE)}) {headers}',read=False)
    _different(query,headers,f'SELECT {names(schema.SOURCE)} FROM {database}.reference_source_v1 FINAL WHERE family=\'sec_original_qualified\' AND entity_id={literal(verified["cik"])} AND source_id={literal(source_id)}')
    insert_rows(query,database,'reference_batch_receipt_v1',[dict(batch_key=key,source_signature=source_id,
        recipe_hash=recipe,population_hash=population_hash,source_rows=1,fact_rows=len(rows),output_hash=expected_hash,completed_at=stamp)])
    query(f'ALTER TABLE {database}.reference_stage_v1 DROP PARTITION {literal(key)}',read=False)
    return dict(state='published',rows=len(rows),batch_key=key,output_hash=expected_hash,source_id=source_id)
