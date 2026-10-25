"""Shared backfill/forward-fill coverage work queue; absence is never a zero.

Queue completion describes reconciliation, not financial completeness. A
published observation cannot close another feature or accounting period.
"""
from pathlib import Path
import json
import re
from zoneinfo import ZoneInfo
from .v1 import FINANCIAL, financial_sql, contract_hash
from .eligibility import require_eligible
from pipelines.certified_research.contracts import digest, literal, identifier

VERSION = 'fundamentals-recovery-queue-v1'
PERIODIC = {'10-K','10-Q','20-F','40-F','10-K/A','10-Q/A','20-F/A','40-F/A'}
REGISTRATION = {'S-1','S-1/A','F-1','F-1/A','S-11','S-11/A','F-10','F-10/A',
                'S-4','S-4/A','F-4','F-4/A','424B3','424B4','8-K12B'}
DEBT_VARIANTS = {'debt_total','long_term_debt_including_current',
                 'long_term_debt_and_finance_leases','debt_current','debt_noncurrent'}


def validate_ids(ciks):
    if not ciks or any(not re.fullmatch('[0-9]{10}', c) for c in ciks):
        raise ValueError('Recovery requires explicit ten-digit issuer identities')
    require_eligible('issuer_cik',ciks)


def source_inventory(query, ciks, cutoff, database='arte'):
    """Exact merge-neutral metadata fingerprint, not a numeric fact rescan.

    Keep individual hashes rather than a lossy sum or latest timestamp. Changes
    to an older retained document must also invalidate an unchanged-case cache.
    """
    from .queries import _timestamp
    identifier(database);validate_ids(ciks);stamp=_timestamp(cutoff)
    if len(ciks)>32:raise ValueError('Recovery metadata cohort exceeds 32 issuers')
    ids=','.join(map(literal,sorted(set(ciks))))
    rows=query(f'''SELECT DISTINCT cik,kind,key,hash FROM (
      SELECT cik,'filing' kind,accession_number key,
        toString(tuple(source_content_sha256,form_type,filing_date,acceptance_datetime_raw,primary_document)) hash
      FROM q_live.sec_filing_v3 FINAL WHERE cik IN ({ids})
      UNION ALL SELECT cik,'document' kind,toString(tuple(accession_number,document_name)) key,
        toString(tuple(content_sha256,source_version_key,document_type,source_archive_date)) hash
      FROM q_live.sec_filing_text_v3 WHERE cik IN ({ids})
      UNION ALL SELECT entity_id cik,'normalized' kind,source_id key,
        toString(tuple(source_hash,recipe_hash,available_at,available_at<={stamp},quality,reason)) hash
      FROM {database}.reference_source_v1 FINAL WHERE entity_kind='issuer_cik' AND entity_id IN ({ids}))
      ORDER BY cik,kind,key,hash''')
    groups={c:[] for c in ciks}
    for row in rows:
        if row['cik'] not in groups:raise ValueError('Source inventory escaped issuer scope')
        groups[row['cik']].append(row)
    return {c:digest(v) for c,v in groups.items()}


def dependency_inventory(query, database):
    """Invalidate on changes to serving joins not represented by source headers.

    These bounded part metadata reads may cause a conservative extra audit on
    a merge. They never allow cached coverage to hide changed period evidence.
    """
    pairs=[('sec_core','sec_bulk_mirror_xbrl_fact_v3'),('q_live','sec_xbrl_company_fact_v3'),
           (database,'reference_period_evidence_v1'),(database,'reference_joint_issuer_v1')]
    scope=','.join('('+literal(d)+','+literal(t)+')' for d,t in pairs)
    mutations=query(f'SELECT database,table,mutation_id FROM system.mutations WHERE NOT is_done AND (database,table) IN ({scope})')
    if mutations:raise ValueError('Recovery serving dependencies have pending mutations')
    return digest(query(f'''SELECT database,table,name,rows,hash_of_all_files FROM system.parts
        WHERE active AND (database,table) IN ({scope}) ORDER BY database,table,name'''))


def coverage_query(ciks, cutoff, database='arte'):
    """One bounded server-side aggregation; never transfer historical values."""
    return f'''SELECT cik,period_start,period_end,unit,
        arraySort(groupUniqArrayIf(feature,NOT conflict)) present,
        arraySort(groupUniqArrayIf(feature,conflict)) conflicts,
        arraySort(groupUniqArray(accession)) accessions
        FROM ({financial_sql(ciks,cutoff,database=database)})
        GROUP BY cik,period_start,period_end,unit
        ORDER BY cik,period_end,period_start,unit'''


def filing_query(ciks, start, end):
    require_eligible('issuer_cik',ciks)
    if not 1<=len(ciks)<=128:raise ValueError('Bound issuer cohort to 128')
    return ('SELECT cik,arraySort(groupUniqArray(form_type)) forms,count() filings,'
        'min(filing_date) first_filing,max(filing_date) last_filing '
        'FROM q_live.sec_filing_v3 FINAL WHERE cik IN ('+','.join(map(literal,sorted(set(ciks))))+') '
        'AND filing_date BETWEEN toDate('+literal(start)+') AND toDate('+literal(end)+') GROUP BY cik')


def native_query(ciks, cutoff, database='arte'):
    from .queries import _timestamp
    identifier(database);validate_ids(ciks)
    ids=','.join(map(literal,sorted(set(ciks))))
    return f'''SELECT entity_id cik,uniqExact(tuple(source_id,metric,context_id)) observations
        FROM {database}.reference_fact_v1 FINAL
        WHERE entity_kind='issuer_cik' AND entity_id IN ({ids}) AND startsWith(metric,'html-native|')
        AND source_id IN (SELECT source_id FROM {database}.reference_source_v1 FINAL
            WHERE entity_kind='issuer_cik' AND entity_id IN ({ids}) AND family='sec_original_qualified'
            AND available_at<={_timestamp(cutoff)}) GROUP BY cik'''


def reviewed_documents(query, ciks, start, end, database='arte'):
    """Queue only source-bound reviewed mappings not already published.

    Unknown HTML stays a semantic review case. Inspect metadata only: adding a
    reviewed mapping in a later release makes its exact source eligible without
    reparsing every blocked original. Existing qualified documents are immutable.
    """
    from pipelines.certified_research.sec_html_reviewed import plan_for
    from pipelines.certified_research.sec_trust_context import registered
    identifier(database);validate_ids(ciks);out=[]
    for offset in range(0,len(ciks),32):
        ids=','.join(map(literal,sorted(set(ciks[offset:offset+32]))))
        rows=query(f'''SELECT DISTINCT cik,accession_number accession,document_name document,
            content_sha256,source_archive_date day,document_type form,source_version_key
            FROM q_live.sec_filing_text_v3 WHERE cik IN ({ids})
            AND source_archive_date BETWEEN toDate({literal(start)}) AND toDate({literal(end)})
            AND (cik,accession_number,document_name) NOT IN (
                SELECT entity_id,event_key,JSONExtractString(evidence_ref,'document')
                FROM {database}.reference_source_v1 FINAL WHERE entity_kind='issuer_cik'
                AND entity_id IN ({ids}) AND family='sec_original_qualified'
                AND source_id IN (SELECT source_signature FROM {database}.reference_batch_receipt_v1 FINAL))
            ORDER BY cik,accession,document,content_sha256''')
        for row in rows:
            if row['cik'] not in ciks[offset:offset+32]:raise ValueError('Reviewed document escaped recovery scope')
            if plan_for(row['content_sha256'],row['cik']) is not None or registered(row['content_sha256'],row['cik']):out.append(row)
    keys=[(d['cik'],d['accession'],d['document']) for d in out]
    if len(set(keys))!=len(keys):raise ValueError('Reviewed document has competing retained occurrences')
    return out


def reconcile(ciks, coverage, filings, native=()):
    require_eligible('issuer_cik',ciks)
    validate_ids(ciks)
    allowed=set(ciks);by_cik={c:[] for c in allowed};metadata={}
    for row in filings:
        if row['cik'] not in allowed or row['cik'] in metadata:raise ValueError('Competing filing inventory')
        metadata[row['cik']]=row
    for row in coverage:
        if row['cik'] not in allowed:raise ValueError('Coverage escaped admitted issuers')
        expected_kind='instant' if row['period_start'] is None else 'duration'
        # Monetary, share and per-share periods must not be mixed.
        expected=[f for f,(kind,_) in FINANCIAL.items() if kind==expected_kind
                  and (('shares' in f and row['unit']=='shares')
                    or (f=='eps_diluted' and row['unit'].endswith('/shares'))
                    or ('shares' not in f and f!='eps_diluted' and len(row['unit'])==3))]
        present=set(row['present']);conflicts=set(row['conflicts'])
        if (present|conflicts)-set(FINANCIAL):raise ValueError('Unknown selected feature')
        absent=set(expected)-present-conflicts
        # Different debt definitions are not simultaneous mandatory fields.
        # Preserve every observed definition; never sum them or infer zero.
        by_cik[row['cik']].append(dict(row,missing_candidates=sorted(absent-DEBT_VARIANTS),
            debt_review_required=bool(expected_kind=='instant' and len(row['unit'])==3
                                      and not ((present|conflicts)&DEBT_VARIANTS)),
            unreported_debt_variants=sorted(absent&DEBT_VARIANTS)))
    native_counts={}
    for row in native:
        if row['cik'] not in allowed or row['cik'] in native_counts:raise ValueError('Native inventory escaped issuer scope')
        native_counts[row['cik']]=int(row['observations'])
    out=[]
    for cik in sorted(allowed):
        contexts=by_cik[cik];meta=metadata.get(cik,{});forms=set(meta.get('forms',[]))
        conflicts=sum(len(x['conflicts']) for x in contexts)
        missing=sum(len(x['missing_candidates']) for x in contexts)
        lane=('native_accounting' if not contexts and native_counts.get(cik,0) else
              'identity' if not forms or not forms & (PERIODIC|REGISTRATION) else
              'extraction' if not contexts or missing or conflicts else 'reconciliation')
        reason=('source_native_measures_not_standard_financials' if lane=='native_accounting' else
                'source_identity_or_reporting_channel_review' if lane=='identity' else
                'conflicting_values' if conflicts else 'feature_period_review' if contexts and missing else
                'no_selected_public_observations' if not contexts else 'observed_contexts_covered_history_unverified')
        body=dict(cik=cik,lane=lane,reason=reason,contexts=contexts,filings=meta,
                  missing_candidates=missing,conflicts=conflicts,native_observations=native_counts.get(cik,0),
                  full_history_complete=False,requires_review=True)
        out.append(dict(body,case_hash=digest(body)))
    return out


def write_queue(query, root, ciks, start, end, cutoff, *, database='arte',
                save, progress=lambda done,total:None):
    """Durable source-scoped queue shared by both launcher modes.

    Preserve unchanged case files byte-for-byte; carry unresolved cases across
    forward windows. No writer or extraction runs behind this audit function.
    """
    validate_ids(ciks)
    root=Path(root);root.mkdir(parents=True,exist_ok=True);cases=root/'cases';cases.mkdir(exist_ok=True)
    ids=sorted(set(ciks));updated=0;unchanged=0
    request=dict(version=VERSION,feature_contract=contract_hash(),population_hash=digest(ids),
                 start=str(start),end=str(end),database=database)
    request_path=root/'request.json'
    if request_path.exists() and json.loads(request_path.read_text())!=request:
        raise ValueError('Recovery request changed; preserve cases and review explicit migration')
    save(request_path,request)
    dependency=dependency_inventory(query,database)
    # Coverage is historical-public as-of. Advancing a UTC date can expose a
    # newly public observation without an insertion; conservatively re-audit.
    from . import v1, reporting_notices
    cache_scope=dict(dependency=dependency,review_contract=digest((Path(__file__).read_text(),Path(v1.__file__).read_text(),Path(reporting_notices.__file__).read_text())),
                     public_day=cutoff.date().isoformat(),
                     public_ny_day=cutoff.astimezone(ZoneInfo('America/New_York')).date().isoformat())
    for offset in range(0,len(ids),32):
        chunk=ids[offset:offset+32]
        # Exact metadata inventory also detects late arrival with an old recorded
        # timestamp. No time-window-only filter may lose an unresolved filing.
        before=source_inventory(query,chunk,cutoff,database)
        prior={c:json.loads((cases/(c+'.json')).read_text()) if (cases/(c+'.json')).exists() else None for c in chunk}
        for c,p in prior.items():
            if p:
                body={k:v for k,v in p.items() if k not in ('case_hash','source_fingerprint','cache_scope')}
                if p.get('cik')!=c or digest(body)!=p.get('case_hash'):
                    raise ValueError('Recovery case receipt changed; preserve for review')
        selected=[c for c in chunk if not prior[c] or prior[c].get('source_fingerprint')!=before[c]
                  or prior[c].get('cache_scope')!=cache_scope]
        unchanged+=len(chunk)-len(selected)
        if selected:
            values=reconcile(selected,query(coverage_query(selected,cutoff,database)),query(filing_query(selected,start,end)),
                             query(native_query(selected,cutoff,database)))
            notices=reporting_notices.inspect_notices(query,selected,cutoff)
            for value in values:
                if notices[value['cik']]:
                    value['reporting_notices']=notices[value['cik']]
                    value['case_hash']=digest({k:v for k,v in value.items() if k!='case_hash'})
            after=source_inventory(query,chunk,cutoff,database)
            if before!=after:raise ValueError('Recovery sources changed during reconciliation; retry without resetting cases')
            for value in values:
                path=cases/(value['cik']+'.json')
                save(path,dict(value,source_fingerprint=before[value['cik']],cache_scope=cache_scope));updated+=1
        progress(min(offset+32,len(ids)),len(ids))
    if dependency_inventory(query,database)!=dependency:
        raise ValueError('Recovery serving dependencies changed during audit; retry preserves completed cases')
    summary=dict(version=VERSION,feature_contract=contract_hash(),population_hash=digest(ids),
        start=str(start),end=str(end),cutoff=cutoff.isoformat(),updated=updated,unchanged=unchanged,
        admitted_issuers=len(ids),pending_review=len(ids),full_backfill_complete=False,
        meaning='Missing candidates require evidence review; no absence or one observed value proves complete history.')
    save(root/'summary.json',summary)
    return summary
