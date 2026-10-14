"""Bounded retained submission-vintage/fragment diagnostics; never certification."""
from datetime import date,datetime,timedelta
import re

from .contracts import digest,literal


def _read(query,table,columns,predicate,limit):
    base=f'FROM sec_core.{table} FINAL PREWHERE {predicate}'
    summary=(f'SELECT count() n,sum(length(toJSONString(tuple({columns})))) bytes,'
        f'lower(hex(SHA256(toJSONString(arraySort(groupArray(lower(hex(SHA256(toJSONString(tuple({columns}))))))))))) source_hash {base}')
    before=query(summary)
    if len(before)!=1 or int(before[0]['n'])>limit or int(before[0]['bytes'])>16*1024**2:
        raise ValueError('Submission coverage metadata exceeds bounded scope')
    rows=query(f'SELECT {columns} {base} ORDER BY {columns} LIMIT {limit+1}')
    if len(rows)!=int(before[0]['n']) or query(summary)!=before:
        raise ValueError('Submission coverage evidence changed during read')
    return rows,before[0]['source_hash']


def audit(query,census):
    body=dict(census);sealed=body.pop('census_hash',None)
    if sealed!=digest(body) or census.get('source')!='bulk_submissions':
        raise ValueError('Independent submission census fingerprint required')
    ciks=census['ciks'];start=date.fromisoformat(census['start']);end=date.fromisoformat(census['end'])
    aliases=','.join(literal(v) for v in sorted({v for cik in ciks for v in (cik,cik.zfill(10))}))
    refs,refs_hash=_read(query,'sec_bulk_mirror_submission_file_ref_v3',
        'cik,file_name,filing_count,filing_from,filing_to,source_file_id',f'cik IN ({aliases})',4096)
    required=[];issues=[]
    for ref in refs:
        if not re.fullmatch(r'CIK[0-9]{10}-submissions-[0-9]+\.json',ref['file_name']):
            issues.append(dict(reason='invalid_fragment_name',cik=ref['cik'],member=ref['file_name']));continue
        lo=date.fromisoformat(str(ref['filing_from'])) if ref['filing_from'] else None
        hi=date.fromisoformat(str(ref['filing_to'])) if ref['filing_to'] else None
        if lo is None or hi is None or lo>hi:
            issues.append(dict(reason='unknown_fragment_interval',cik=ref['cik'],member=ref['file_name']));required.append(ref)
        elif lo<=end and hi>=start:required.append(ref)
    parents=tuple('CIK'+cik.zfill(10)+'.json' for cik in ciks)
    names=tuple(sorted(set(parents)|{r['file_name'] for r in required}))
    members,members_hash=_read(query,'sec_bulk_mirror_member_manifest_v1',
        'member_name,cik,source_file_id,member_modified_at_utc,status,member_signature,member_file_size',
        "source_name='submissions' AND member_name IN ("+','.join(map(literal,names))+')',8192)
    source_ids={r['source_file_id'] for r in census['documents']}|{r['source_file_id'] for r in required}
    by_name={name:[r for r in members if r['member_name']==name and r['source_file_id'] in source_ids] for name in names}
    for name in names:
        candidates=by_name[name]
        if len(candidates)!=1:
            issues.append(dict(reason='missing_or_ambiguous_member',member=name,count=len(candidates)));continue
        item=candidates[0]
        if item['status']!='completed' or not item['member_signature'] or int(item['member_file_size'])<=0:
            issues.append(dict(reason='unverified_member_ingestion',member=name))
        if name in parents:
            raw=item['member_modified_at_utc']
            modified=datetime.fromisoformat(str(raw)).date() if raw else None
            # ZIP metadata is diagnostic, not independent public-availability proof.
            if modified is None or modified<end+timedelta(days=1):
                issues.append(dict(reason='parent_vintage_does_not_cover_end',member=name,modified=raw,required_through=str(end)))
        for ref in required:
            if ref['file_name']==name and ref['source_file_id']!=item['source_file_id']:
                issues.append(dict(reason='fragment_source_differs_from_reference',member=name))
    result=dict(version='retained-sec-submission-coverage-audit-v1',census_hash=sealed,
        reference_source_hash=refs_hash,member_source_hash=members_hash,parents=len(parents),
        required_fragments=len(required),issues=tuple(issues),retained_evidence_ready=not issues,
        coverage_certified=False,limitation='ZIP member vintage and ingestion manifests are diagnostics; independent acquisition completeness remains required.')
    return dict(result,audit_hash=digest(result))


def inspect(query,ciks,start,end,*,forms,maximum_documents=1000):
    """Inspect both authorities as one bounded, read-only prerequisite report."""
    from .sec_filing_census import inventory,reconcile
    options=dict(forms=forms,maximum_documents=maximum_documents)
    retained=inventory(query,ciks,start,end,**options)
    submissions=inventory(query,ciks,start,end,source='bulk_submissions',**options)
    reconciliation=reconcile(retained,submissions)
    coverage=audit(query,submissions)
    for expected,source in ((retained,'retained'),(submissions,'bulk_submissions')):
        if inventory(query,ciks,start,end,source=source,**options)['census_hash']!=expected['census_hash']:
            raise ValueError('SEC prerequisite inventory changed during inspection')
    if audit(query,submissions)['audit_hash']!=coverage['audit_hash']:
        raise ValueError('SEC prerequisite coverage changed during inspection')
    ready=reconciliation['identity_reconciled'] and coverage['retained_evidence_ready'] and bool(retained['documents'])
    result=dict(version='retained-sec-prerequisite-inspection-v1',status='retained_evidence_ready' if ready else 'blocked',
        ciks=retained['ciks'],start=retained['start'],end=retained['end'],forms=retained['forms'],
        reconciliation=reconciliation,coverage=coverage,coverage_certified=False,
        retained_issues=retained['issue_counts'],submission_issues=submissions['issue_counts'],
        next_requirement='Independent acquisition completeness and original document/public-time qualification; no download or write performed.')
    return dict(result,inspection_hash=digest(result))
