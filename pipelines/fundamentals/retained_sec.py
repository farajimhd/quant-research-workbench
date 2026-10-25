"""Retained SEC originals under the explicitly approved two-clock policy.

Acceptance is a labelled historical proxy, not a claimed posting timestamp.
The current ingest timestamp is never substituted for historical availability.
Archive-qualified authorities and their receipts are not rewritten.
"""
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo
import json
import re

from . import qualified, schema
from .eligibility import require_eligible
from pipelines.certified_research import sec_document_payload, sec_fundamental_context, sec_precision
from pipelines.certified_research.contracts import digest, identifier, literal

VERSION = 'retained-sec-acceptance-proxy-v1'
NY = ZoneInfo('America/New_York')


def historical_time(metadata):
    """Reject unknown/special chronology rather than inventing a publication time."""
    raw = metadata.get('acceptance_datetime_raw') or ''
    if re.fullmatch(r'[0-9]{14}', raw):
        naive = datetime.strptime(raw, '%Y%m%d%H%M%S')
        accepted = naive.replace(tzinfo=NY)
        if (accepted.utcoffset() != accepted.replace(fold=1).utcoffset()
                or accepted.astimezone(timezone.utc).astimezone(NY).replace(tzinfo=None) != naive):
            raise ValueError('Ambiguous or invalid SEC acceptance timezone')
    elif metadata.get('time_metadata_source') == 'sec_filing_v3' and re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?Z', raw):
        accepted = datetime.fromisoformat(raw.replace('Z','+00:00')).astimezone(NY)
    else:
        raise ValueError('SEC acceptance timestamp is absent or malformed')
    filing = datetime.strptime(metadata.get('filing_date') or '', '%Y-%m-%d').date()
    if (metadata.get('private_to_public') or metadata.get('pac_event_id')
            or int(metadata.get('public_document_count') or 0) <= 0):
        raise ValueError('Private, corrected or non-public SEC occurrence needs separate review')
    if accepted.date() > filing or (filing - accepted.date()).days > 7:
        raise ValueError('SEC filing and acceptance chronology conflicts')
    # A later official filing day must not be pulled back to late acceptance.
    available = max(accepted, datetime.combine(filing, time(), NY))
    return dict(available_at=available.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f'),
                acceptance_at=accepted.astimezone(timezone.utc).isoformat(), filing_date=str(filing),
                availability_basis='sec_acceptance_proxy' if available == accepted else 'sec_later_filing_day',
                exact_publication_time=False, policy=VERSION,
                recorded_time_basis='ingestion_time_fallback_no_download_receipt')


def inspect(query, document):
    cik, accession, name = (document[k] for k in ('cik', 'accession', 'document'))
    require_eligible('issuer_cik', [cik])
    if (not re.fullmatch(r'[0-9]{10}', cik) or not re.fullmatch(r'[0-9]{10}-[0-9]{2}-[0-9]{6}', accession)
            or not isinstance(name, str) or not 1 <= len(name) <= 256):
        raise ValueError('Invalid retained document identity')
    where = f'cik={literal(cik)} AND accession_number={literal(accession)} AND document_name={literal(name)}'
    columns = 'cik,accession_number,document_name,document_type,content_sha256,source_version_key,source_archive_date'
    rows = query(f'SELECT DISTINCT {columns},length(source_text) bytes FROM q_live.sec_filing_text_v3 PREWHERE {where} LIMIT 2')
    if len(rows) != 1 or rows[0]['content_sha256'] != document['content_sha256']:
        raise ValueError('Retained document is absent, competing or changed')
    source = rows[0]
    if ((document.get('source_version_key') is not None and document['source_version_key'] != source['source_version_key'])
            or (document.get('day') is not None and document['day'] != str(source['source_archive_date']))
            or (document.get('form') is not None and document['form'] != source['document_type'])):
        raise ValueError('Retained document differs from the sealed version, date or form')
    if not 0 < int(source['bytes']) <= 64 * 1024**2:
        raise ValueError('Retained document exceeds bounded payload size')
    version_scope = ' AND source_version_key=' + literal(source['source_version_key']) if source['source_version_key'] else ''
    metadata = query('SELECT DISTINCT primary_cik,form_type,filing_date,acceptance_datetime_raw,'
                     'public_document_count,private_to_public,pac_event_id,source_content_sha256,source_version_key '
                     'FROM q_live.sec_filing_archive_accession_v3 WHERE accession_number=' + literal(accession)
                     + version_scope + ' LIMIT 2')
    exhibit=bool(re.fullmatch(r'EX-[0-9]+(?:\.[0-9]+)?',source['document_type']))
    if (len(metadata) != 1 or str(int(metadata[0]['primary_cik'])).zfill(10) != cik
            or (metadata[0]['form_type'] != source['document_type'] and not exhibit)):
        raise ValueError('Retained SEC issuer/form metadata is missing or competing')
    if exhibit:
        # An exhibit has its own type, but derives availability from its exact
        # parent occurrence. Require the original retained document membership;
        # an accession-looking filename or an unrelated primary form is not proof.
        if not source['source_version_key'] or not metadata[0]['acceptance_datetime_raw']:
            raise ValueError('Exhibit needs versioned parent acceptance evidence')
        members=query(f'SELECT DISTINCT {columns} FROM q_live.sec_filing_document_v3 WHERE {where} LIMIT 2')
        expected={k:source[k] for k in columns.split(',')}
        if members!=[expected]:
            raise ValueError('Retained exhibit membership differs from source occurrence')
        metadata[0]=dict(metadata[0],exhibit_member=members[0])
    if not metadata[0]['acceptance_datetime_raw']:
        # Legacy text rows predate occurrence keys. Bind a unique retained
        # submissions record by the exact primary document and issuer, never ticker.
        times = query('SELECT DISTINCT cik,form_type,filing_date,primary_document,acceptance_datetime_raw,'
                      'source_content_sha256 FROM q_live.sec_filing_v3 FINAL WHERE cik='+literal(cik)
                      +' AND accession_number='+literal(accession)+' LIMIT 2')
        if (len(times)!=1 or times[0]['primary_document']!=name
                or times[0]['form_type']!=metadata[0]['form_type']
                or times[0]['filing_date']!=metadata[0]['filing_date']
                or not re.fullmatch('[0-9a-f]{64}',times[0]['source_content_sha256'])):
            raise ValueError('Legacy SEC acceptance metadata is missing or competing')
        metadata[0]=dict(metadata[0], acceptance_datetime_raw=times[0]['acceptance_datetime_raw'],
                         time_metadata_source='sec_filing_v3',time_metadata_evidence=times[0])
    return dict(document=source, metadata=metadata[0], time_evidence=historical_time(metadata[0]))


def prepare(query, document, claim, guard=lambda: None):
    claim.check(); guard()
    before = inspect(query, document)
    payload = sec_document_payload.load(query, document['cik'], document['accession'], document['document'],
        document['content_sha256'], maximum_bytes=64*1024**2, guard=claim.check)
    extracted = sec_fundamental_context.extract(payload, document['content_sha256'], document['cik'], maximum_bytes=64*1024**2)
    del payload
    # Exact serialization readback, registered HTML semantics and precision
    # checks are shared with the archive-qualified original path.
    extracted = json.loads(json.dumps(extracted))
    if (extracted['source_text_hash'] != document['content_sha256']
            or digest({k:v for k,v in extracted.items() if k!='extraction_hash'}) != extracted['extraction_hash']):
        raise ValueError('Extracted original hash differs')
    rejected = [r for r in extracted['rejected'] if not (r['reason'] == 'nil_share_fact'
                and extracted.get('version') == 'sec-inline-fundamental-context-v4')]
    if rejected or extracted['security_cover']['rejected']:
        raise ValueError('Unresolved original numeric or security identity contexts')
    if extracted.get('version') == 'sec-reviewed-html-context-v1':
        from pipelines.certified_research.sec_html_context import verify_registered
        verify_registered(extracted, document['cik'])
    projection = sec_precision.select_reported(extracted, mode='round-to-nearest')
    if not projection['groups']:
        raise ValueError('No selected fundamental observations in this original')
    claim.check(); guard()
    if inspect(query, document) != before:
        raise ValueError('Retained SEC metadata changed during extraction')
    result = dict(version=VERSION, cik=document['cik'], accession=document['accession'], document=document['document'],
        document_hash=document['content_sha256'], source_table='q_live.sec_filing_text_v3',
        lineage_hash=digest(before), extraction_hash=extracted['extraction_hash'],
        projection_hash=projection['projection_hash'], groups=projection['groups'],
        available_at=before['time_evidence']['available_at'], time_evidence=before['time_evidence'],
        qualification_scope='reviewed_html_observations' if extracted.get('version') == 'sec-reviewed-html-context-v1' else 'selected_tagged_observations',
        whole_scope_certified=False, aggregation='none')
    native = {r['concept']:r['html_proof']['native'] for r in extracted['observations']
              if 'native' in r.get('html_proof', {})}
    if native: result['native_definitions'] = native
    return dict(result, authority_hash=digest(result)), before


def publish(query, database, document, population_hash, observed_at, claim, guard=lambda: None):
    identifier(database)
    if observed_at.tzinfo is None: raise ValueError('Aware ingestion timestamp required')
    schema.verify(query, database)
    verified, before = prepare(query, document, claim, guard)
    prior = query(f"SELECT DISTINCT source_id FROM {database}.reference_source_v1 FINAL "
        "WHERE family='sec_original_qualified' AND entity_id="+literal(document['cik'])+
        ' AND event_key='+literal(document['accession'])+
        ' AND JSONExtractString(evidence_ref,\'document\')='+literal(document['document']))
    if any(r['source_id'] != verified['authority_hash'] for r in prior):
        raise ValueError('Existing qualified document is preserved; explicit time-policy migration required')
    def stable():
        claim.check(); guard()
        if inspect(query, document) != before:
            raise ValueError('Retained original changed before publication')
    result = qualified._publish_verified(query, 'q_live', database,
        (document['cik'], document['accession'], document['document']), verified['authority_hash'],
        population_hash, observed_at, claim, stable, verified)
    return dict(result, time_evidence=verified['time_evidence'], qualification_scope=verified['qualification_scope'])
