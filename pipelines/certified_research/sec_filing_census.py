"""Bounded retained filing census; source discovery is not coverage certification."""
from collections import Counter
from datetime import date
import re

from .contracts import digest,literal


def inventory(query,ciks,start,end,*,forms,maximum_documents=10000):
    if (not isinstance(ciks,(tuple,list)) or not 1<=len(ciks)<=256 or any(not re.fullmatch('0*[1-9][0-9]{0,9}',str(cik)) for cik in ciks)
            or type(start) is not date or type(end) is not date or start>end
            or not isinstance(forms,(tuple,list)) or not 1<=len(forms)<=32 or len(set(forms))!=len(forms)
            or any(not isinstance(form,str) or not re.fullmatch('[A-Z0-9 -]{1,24}(?:/A)?',form) for form in forms)
            or type(maximum_documents) is not int or not 1<=maximum_documents<=10000):
        raise ValueError('Invalid bounded retained filing census scope')
    normalized=tuple(sorted({str(int(cik)) for cik in ciks}))
    if len(normalized)!=len(ciks):raise ValueError('Filing census has duplicate normalized issuers')
    aliases=','.join(literal(v) for v in sorted({value for cik in normalized for value in (cik,cik.zfill(10))}))
    columns='cik,accession_number,form_type,filing_date,primary_document,source_content_sha256'
    where=(f'cik IN ({aliases}) AND form_type IN ({",".join(map(literal,forms))}) AND '
        f'(filing_date BETWEEN toDate({literal(start)}) AND toDate({literal(end)}) OR filing_date IS NULL)')
    base=f'FROM q_live.sec_filing_v3 FINAL WHERE {where}'
    census_sql=(f'SELECT count() n,sum(length(toJSONString(tuple({columns})))) bytes,'
        f'lower(hex(SHA256(toJSONString(arraySort(groupArray(lower(hex(SHA256(toJSONString(tuple({columns}))))))))))) source_hash {base}')
    before=query(census_sql)
    if len(before)!=1 or int(before[0]['n'])>maximum_documents or int(before[0]['bytes'])>16*1024**2:
        raise ValueError('Retained filing census exceeds document/metadata bound')
    rows=query(f'SELECT {columns} {base} ORDER BY cik,accession_number LIMIT {maximum_documents+1}')
    if len(rows)!=int(before[0]['n']) or query(census_sql)!=before:
        raise ValueError('Retained filing census changed while reading')
    documents=[];issues=[];seen=set()
    for row in rows:
        cik=str(int(row['cik']));accession=row['accession_number'];key=(cik,accession)
        filed=date.fromisoformat(str(row['filing_date'])) if row['filing_date'] is not None else None
        if cik not in normalized or row['form_type'] not in forms or (filed is not None and not start<=filed<=end):
            raise ValueError('Retained filing census returned out-of-scope metadata')
        if key in seen:raise ValueError('Retained filing census has duplicate accession identity')
        seen.add(key);reasons=[]
        if not re.fullmatch('[0-9]{10}-[0-9]{2}-[0-9]{6}',accession):reasons.append('invalid_accession')
        if row['filing_date'] is None:reasons.append('unknown_filing_date')
        if not row['primary_document']:reasons.append('missing_primary_document')
        elif len(row['primary_document'])>256:reasons.append('oversized_primary_document_name')
        if not re.fullmatch('[0-9a-f]{64}',row['source_content_sha256'] or ''):reasons.append('missing_filing_source_hash')
        record=dict(cik=cik,accession=accession,form=row['form_type'],filing_date=row['filing_date'],
            primary_document=row['primary_document'],filing_source_hash=row['source_content_sha256'])
        if reasons:issues.append(dict(record,reasons=tuple(reasons)))
        else:documents.append(record)
    result=dict(version='retained-sec-primary-filing-census-v1',ciks=normalized,start=str(start),end=str(end),forms=tuple(sorted(forms)),
        filings=len(rows),documents=tuple(documents),unresolved=tuple(issues),
        issue_counts=dict(sorted(Counter(reason for row in issues for reason in row['reasons']).items())),
        source_hash=before[0]['source_hash'],discovery_ready=not issues,coverage_certified=False,
        limitation='Retained filing inventory only; independent acquisition/submission completeness and document/time verification remain required.')
    return dict(result,census_hash=digest(result))
