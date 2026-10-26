"""Bounded, shared original extraction. No API calls or historical scans.

Results use one ingestion-owned original-fact table with distinct source identity.
Canonical ingestion completeness is separate from selected-fact extraction.
"""
from hashlib import sha256
from pathlib import Path
from datetime import datetime, timezone
import json
import math
import re
from functools import lru_cache

from pipelines.certified_research import sec_fundamental_context, sec_precision, sec_taxonomy, facts
from pipelines.certified_research.contracts import digest
from .public_time import historical_time
from .original_schema import TABLE,FACT_COLUMNS

VERSION = 'sec-ingested-selected-original-v1'


@lru_cache(maxsize=1)
def recipe():
    from pipelines.certified_research import sec_share_context, sec_html_reviewed, sec_html_context, sec_taxonomy, sec_trust_context
    modules = (sec_fundamental_context, sec_precision, sec_share_context, sec_html_reviewed, sec_html_context, sec_taxonomy, sec_trust_context)
    return digest((VERSION, facts.catalog_hash(facts.catalog()),
                   sha256(Path(__file__).read_bytes()).hexdigest(),
                   [sha256(Path(m.__file__).read_bytes()).hexdigest() for m in modules]))


def extract_document(payload, content_hash, cik, *, resources=(), cache_root=None):
    """Reuse only byte/recipe-bound results; malformed facts never become zeros."""
    if sha256(payload.encode('utf-8')).hexdigest() != content_hash:
        raise ValueError('Original document hash differs before extraction')
    from pipelines.certified_research.sec_html_reviewed import plan_for
    key = digest((recipe(), cik, content_hash, resources,plan_for(content_hash,cik)))
    path = None if cache_root is None else Path(cache_root) / (key + '.json')
    if path is not None and path.exists():
        saved = json.loads(path.read_text(encoding='utf-8'))
        if saved['key'] != key or digest(saved['result']) != saved['result_hash']:
            raise ValueError('Original extraction cache integrity differs')
        return saved['result']
    try:
        result=_compute(payload,content_hash,cik,resources)
    except (ValueError,KeyError,TypeError) as error:
        result=dict(status='review_required',groups=[],reason=str(error)[:500])
    result = json.loads(json.dumps(result))
    if path is not None:
        from uuid import uuid4
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.'+uuid4().hex+'.tmp')
        temporary.write_text(json.dumps(dict(key=key,result=result,result_hash=digest(result))),encoding='utf-8')
        temporary.replace(path)
    return result


def _compute(payload,content_hash,cik,resources):
    extraction = sec_fundamental_context.extract(payload, content_hash, cik,
        maximum_bytes=64*1024**2, resource_documents=resources)
    if digest({k:v for k,v in extraction.items() if k != 'extraction_hash'}) != extraction['extraction_hash']:
        raise ValueError('Original extraction envelope differs')
    rejected = [r for r in extraction['rejected'] if not (
        r['reason'] == 'nil_share_fact' and extraction['version'] == 'sec-inline-fundamental-context-v4')]
    if rejected or extraction['security_cover']['rejected']:
        reasons=sorted({r['reason'] for r in rejected+list(extraction['security_cover']['rejected'])})
        raise ValueError('Unresolved original numeric or security contexts: '+','.join(reasons))
    if extraction['version'] == 'sec-reviewed-html-context-v1':
        from pipelines.certified_research.sec_html_context import verify_registered
        verify_registered(extraction, cik)
    if not extraction['observations']:
        result = dict(status='no_selected_facts', groups=[], extraction_hash=extraction['extraction_hash'])
    else:
        projected = sec_precision.select_reported(extraction, mode='round-to-nearest')
        result = dict(status='extracted', groups=projected['groups'],
                      extraction_hash=extraction['extraction_hash'], projection_hash=projected['projection_hash'])
    return result


def extract_accession(documents, metadata, *, cik, accession, inserted_at, source_run_id, cache_root=None):
    rows=[]; outcomes=[]
    try:
        time_evidence = historical_time(metadata)
    except ValueError as error:
        return [], [dict(status='review_required',reason=str(error),document='')]
    by_name={d['document_name']:d for d in documents}
    if len(by_name) != len(documents):
        raise ValueError('Duplicate original document names')
    for doc in documents:
        payload=doc['source_text'];content_hash=doc['content_sha256'];name=doc['document_name']
        from pipelines.certified_research.sec_html_reviewed import plan_for
        # Do not parse ordinary narrative HTML or every note/exhibit repeatedly.
        tagged=('inlineXBRL' in payload or bool(re.search(r'<(?:[A-Za-z_][\w.-]*:)?xbrl(?:\s|>)',payload,re.I)))
        reviewed=plan_for(content_hash,cik) is not None
        if not tagged and not reviewed:
            if doc.get('document_role')=='primary_filing':
                outcomes.append(dict(document=name,content_hash=content_hash,status='untagged_review_required',rows=0))
            continue
        try:
            extracted=extract_document(payload,content_hash,cik,cache_root=cache_root)
            if extracted['status']=='review_required' and 'missing_context_or_unit' in extracted['reason']:
                resources=tuple(dict(payload=d['source_text'],source_text_hash=d['content_sha256'])
                    for d in documents if d['content_sha256']!=content_hash and d.get('content_format')=='xml'
                    and re.search(r'<(?:[A-Za-z_][\w.-]*:)?xbrl(?:\s|>)',d['source_text'],re.I))
                if resources:
                    extracted=extract_document(payload,content_hash,cik,resources=resources,cache_root=cache_root)
            if extracted['status']=='review_required':
                raise ValueError(extracted['reason'])
            # Any unsupported taxonomy remains a review case for this document.
            prepared=[]
            for group in extracted['groups']:
                taxonomy,tag=sec_taxonomy.identity(group['concept'],allow_extension=True)
                identity=digest((VERSION,cik,accession,name,group['context_hash'],group['value'],group['decimals'],
                                 metadata.get('source_version_key'),time_evidence))
                if not math.isfinite(float(group['value'])):
                    raise ValueError('Original value exceeds legacy numeric storage range')
                prepared.append(dict(company_fact_id=identity,issuer_id=None,cik=cik,taxonomy=taxonomy,tag=tag,
                    unit_code=group['unit'],fiscal_year=None,fiscal_period=None,
                    filed_at_utc=metadata['filing_date']+' 00:00:00',period_end_date=group['period_end'],
                    period_start_date=group['period_start'],frame=None,
                    value=float(group['value']),value_exact=group['value'],form_type=metadata.get('form_type'),
                    accession_number=accession,recorded_at_utc=inserted_at,source_run_id=source_run_id,
                    source_content_sha256=content_hash,inserted_at=inserted_at,source_kind='original_document',
                    document_name=name,source_positions=list(group['all_source_positions']),
                    dimensions_json=json.dumps(group['dimensions'],separators=(',',':')),
                    public_available_at=time_evidence['available_at'],availability_basis=time_evidence['availability_basis'],
                    extraction_version=VERSION+':'+digest(group)))
                prepared[-1].update(source_version_key=metadata.get('source_version_key',''),
                    source_accession_sha256=metadata.get('source_content_sha256',''),
                    time_evidence_json=json.dumps(dict(time_evidence,metadata_source=metadata.get('time_metadata_source','archive_header'),
                        metadata_evidence=metadata.get('time_metadata_evidence')),sort_keys=True,separators=(',',':')))
            rows.extend(prepared)
            outcomes.append(dict(document=name,content_hash=content_hash,status=extracted['status'],rows=len(prepared)))
        except (ValueError,KeyError,TypeError) as error:
            outcomes.append(dict(document=name,content_hash=content_hash,status='review_required',reason=str(error)[:500]))
    return rows,outcomes


def missing_rows(client,database,rows):
    """Keep first observation clocks and avoid re-inserting completed originals."""
    return _readback(client,database,rows,allow_missing=True)


def verify_readback(client, database, rows):
    _readback(client,database,rows,allow_missing=False)


def _readback(client, database, rows,*,allow_missing):
    """Verify only newly supplied original keys, not the full fact history."""
    from research.mlops.clickhouse import sql_string, quote_ident
    original=[r for r in rows if r.get('source_kind')=='original_document']
    columns=('company_fact_id','source_content_sha256','document_name','value_exact',
             'period_start_date','period_end_date','dimensions_json','source_positions','extraction_version',
             'source_kind','availability_basis','public_available_at')
    columns+=('source_version_key','source_accession_sha256','time_evidence_json')
    select=','.join("toString(toDateTime64(public_available_at,6,'UTC')) public_available_at" if c=='public_available_at' else c for c in columns)
    missing=[]
    for offset in range(0,len(original),512):
        chunk=original[offset:offset+512]
        ids=','.join(sql_string(r['company_fact_id']) for r in chunk)
        where=(f'cik={sql_string(chunk[0]["cik"])} AND accession_number={sql_string(chunk[0]["accession_number"])} '
               f'AND company_fact_id IN ({ids})')
        actual=[json.loads(line) for line in client.execute(
            f'SELECT {select} FROM {quote_ident(database)}.{TABLE} FINAL '
            f'WHERE {where} FORMAT JSONEachRow').splitlines() if line.strip()]
        expected=[{k:r[k] for k in columns} for r in chunk]
        order=lambda r:r['company_fact_id']
        by_id={r['company_fact_id']:r for r in actual}
        if len(by_id)!=len(actual):raise ValueError('Duplicate committed original keys')
        retained=[r for r in expected if r['company_fact_id'] in by_id]
        if sorted(actual,key=order)!=sorted(retained if allow_missing else expected,key=order):
            raise ValueError('Ingested original fact readback differs')
        missing.extend(r for r in chunk if r['company_fact_id'] not in by_id)
    return missing
