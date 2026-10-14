"""Automatically freeze issuer scope locators from retained verified SEC banks."""
from datetime import date
from pathlib import Path
import sys

from . import facts,sec_scope_specification,sec_scope_parity,sec_filing_census,sec_document_lineage,reference_sql
from . import sec_share_preparation,sec_share_source,sec_share_context,sec_fundamental_publication,sec_reported_publication
from . import sec_share_capture,sec_witness_publication,sec_public_archive_time
from .campaign import PlannedUnit
from .contracts import digest,literal
from .reference_publication import ReferenceScope

PRODUCT='sec_scope_specification'
# Gateway financial-profile forms, including the corresponding 6-K amendment.
DEFAULT_FORMS=('10-K','10-K/A','10-Q','10-Q/A','8-K','8-K/A','20-F','20-F/A','40-F','40-F/A','6-K','6-K/A')


def recipe_hash():
    modules=(sys.modules[__name__],sec_scope_specification,sec_scope_parity,sec_filing_census,sec_document_lineage)
    return digest(dict(version='automatic-sec-scope-preparation-v1',forms=DEFAULT_FORMS,catalog=facts.catalog_hash(),
        algorithms={m.__name__:digest(Path(m.__file__).read_text(encoding='utf-8')) for m in modules},
        ddl=tuple(t.ddl('q_live') for t in sec_scope_specification.tables())))


def selection_hash(*,forms=DEFAULT_FORMS,numeric_mode=None,public_time_mode='acceptance_checked_day'):
    return digest(dict(recipe_hash=recipe_hash(),forms=tuple(forms),numeric_mode=numeric_mode,public_time_mode=public_time_mode))


def required_census_start(query,scope,cik,metrics=None):
    selected=tuple(m for m in facts.catalog() if m.tags and (metrics is None or m.name in metrics))
    if not selected or (metrics is not None and set(metrics)!={m.name for m in selected}):
        raise ValueError('Automatic original SEC scope metric selection is invalid')
    projection=reference_sql.sec_projection('00000000-0000-0000-0000-000000000001',
        '00000000-0000-0000-0000-000000000001',scope.entity_key,cik,scope.start,scope.end,metrics=selected,provenance=True)
    aliases=','.join(literal(v) for v in sorted({str(int(cik)),str(int(cik)).zfill(10)}))
    rows=query('SELECT count() n,uniqExact(p.accession) accessions,countIf(f.filing_date IS NULL) unknown_dates,'
        'min(f.filing_date) required_start FROM (SELECT DISTINCT JSONExtractString(proof,4) accession FROM ('+projection+')) p '
        'LEFT JOIN (SELECT accession_number,filing_date FROM q_live.sec_filing_v3 FINAL '
        f'PREWHERE cik IN ({aliases})) f ON f.accession_number=p.accession')
    if (len(rows)!=1 or not 0<int(rows[0]['n'])<=1000 or int(rows[0]['n'])!=int(rows[0]['accessions'])
            or int(rows[0]['unknown_dates']) or rows[0]['required_start'] is None):
        raise ValueError('Selected SEC facts have missing/ambiguous filing-date context')
    return min(scope.start,date.fromisoformat(str(rows[0]['required_start'])))


def discover(query,scope,cik,claim,source_guard,*,forms=DEFAULT_FORMS,numeric_mode=None,public_time_mode='acceptance_checked_day',metrics=None):
    if numeric_mode not in (None,'round-to-nearest','truncation') or public_time_mode not in ('acceptance_checked_day','official_filing_day'):
        raise ValueError('Invalid original SEC scope numeric/public-time selection')
    context_start=required_census_start(query,scope,cik,metrics)
    census=sec_filing_census.inventory(query,(str(int(cik)),),context_start,scope.end,forms=tuple(forms),maximum_documents=1000)
    if not census['discovery_ready'] or not census['documents']:raise ValueError('Original SEC scope filing census is empty or unresolved')
    sec_share_capture.verify(query,'q_live',required=True,catalog=(*sec_fundamental_publication.tables(),
        *sec_reported_publication.tables(),*sec_witness_publication.tables()))
    bindings=[]
    for doc in census['documents']:
        claim.check();source_guard()
        lineage=sec_document_lineage.inspect(query,cik,doc['accession'],doc['primary_document'],claim,source_guard)
        key=digest(dict(version=sec_share_source.VERSION,candidate_hash=lineage['candidate_hash'],parser=sec_share_context.VERSION))
        rows=query('SELECT f.reconciliation_hash FROM q_live.'+sec_fundamental_publication.tables()[-1].name+' f '
            'INNER JOIN q_live.research_sec_document_archive_time_v1 t ON t.qualification_hash=f.qualification_hash '
            f'WHERE f.document_key={literal(key)} AND t.document_key={literal(key)} '
            f'AND f.document_hash={literal(lineage["document"]["content_sha256"])} AND f.catalog_hash={literal(facts.catalog_hash())} '
            f'AND t.mode={literal(public_time_mode)} AND t.policy_version={literal(sec_public_archive_time.VERSION)} LIMIT 2')
        if len(rows)!=1:raise ValueError(f'{doc["accession"]}: current original fundamental binding is missing or ambiguous')
        reconciliation=rows[0]['reconciliation_hash']
        selected=f' AND numeric_mode={literal(numeric_mode)}' if numeric_mode else ''
        projected=query('SELECT binding_hash FROM q_live.research_sec_reported_document_v1 '
            f'WHERE document_key={literal(key)} AND source_reconciliation_hash={literal(reconciliation)}{selected} LIMIT 2')
        if len(projected)!=1:raise ValueError(f'{doc["accession"]}: original reported/public-time binding is missing or ambiguous; select an existing numeric mode')
        bindings.append(dict(accession=doc['accession'],document=doc['primary_document'],reconciliation_hash=reconciliation,
            reported_binding_hash=projected[0]['binding_hash']))
    spec=dict(version='sec-original-scope-specification-v1',entity_key=scope.entity_key,cik=str(int(cik)),
        start=str(scope.start),end=str(scope.end),context_start=str(context_start),source_hash=scope.parent_hash,
        metrics=list(metrics) if metrics is not None else [m.name for m in facts.catalog() if m.tags],forms=list(forms),
        bindings=sorted(bindings,key=lambda r:(r['accession'],r['document'])))
    spec['specification_hash']=digest(spec);sec_scope_specification.records(spec)
    claim.check();source_guard()
    if sec_filing_census.inventory(query,(str(int(cik)),),context_start,scope.end,forms=tuple(forms),maximum_documents=1000)!=census:
        raise ValueError('Original SEC filing census changed during automatic specification discovery')
    return spec


def prepare(query,config,plans,claim,dictionary_factory,profile,source_guard,*,stop=None,progress=None,
            forms=DEFAULT_FORMS,numeric_mode=None,public_time_mode='acceptance_checked_day'):
    plans=tuple(plans);targets=[p for p in plans if p.unit.product=='fundamentals']
    if not targets:return plans
    if len(targets)>8192:raise ValueError('Automatic SEC scope preparation exceeds issuer bound')
    if config.global_memory_bytes<sec_share_preparation.MEMORY+config.query_memory_bytes:
        raise ValueError('Automatic SEC scope verification requires 2GiB plus query memory')
    def guard():
        claim.check();source_guard()
        if stop is not None and stop.is_set():raise InterruptedError('Automatic SEC scope preparation stopped')
    # One bounded scope is published at a time; each insert rechecks actual headroom.
    admit=sec_share_preparation.admission(query,profile,1,1,product=PRODUCT,recipe=recipe_hash())
    guard();admit(0);updated={}
    for index,plan in enumerate(targets):
        guard();a=plan.arguments
        if progress:progress(dict(phase='sec-scope-specification',state='active',entity_key=a['entity_key'],completed=index,total=len(targets),inserted_rows=0))
        scope=ReferenceScope('00000000-0000-0000-0000-000000000001',1,int(a['entity_key']),'fundamentals',
            date.fromisoformat(a['start']),date.fromisoformat(a['end']),a['source_hash'],facts.catalog_hash())
        if 'original_specification_hash' in a:
            spec=sec_scope_specification.load(query,'q_live',a['original_specification_hash'],claim,guard)
        else:spec=discover(query,scope,a['cik'],claim,guard,forms=forms,numeric_mode=numeric_mode,public_time_mode=public_time_mode,metrics=a.get('metrics'))
        evidence=sec_scope_parity.verify(query,scope,spec,claim,guard)
        if not isinstance(evidence,dict) or evidence.get('certified') is not True:raise ValueError('Original SEC scope remains unqualified')
        dictionary=dictionary_factory()
        try:sec_scope_specification.install(query,'q_live',dictionary)
        finally:dictionary.close()
        result=sec_scope_specification.capture(query,'q_live',spec,claim,guard,admit)
        if sec_scope_specification.load(query,'q_live',spec['specification_hash'],claim,guard)!=spec:
            raise ValueError('Automatic original SEC specification publication differs from discovered inputs')
        updated[plan.unit.key]=PlannedUnit(plan.unit,dict(a,original_specification_hash=spec['specification_hash']))
        if progress:progress(dict(phase='sec-scope-specification',state='completed',entity_key=a['entity_key'],completed=index+1,total=len(targets),inserted_rows=result['inserted_rows']))
    guard();return tuple(updated.get(p.unit.key,p) for p in plans)
