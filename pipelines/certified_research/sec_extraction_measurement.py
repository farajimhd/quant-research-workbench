"""Read-only bounded retained SEC extraction measurements, never pilot qualification."""
import json
from collections import Counter
from time import perf_counter

from . import sec_share_preparation,sec_fundamental_context
from .contracts import digest,literal


def measure(query,requests,*,progress=None):
    if not isinstance(requests,(tuple,list)) or not 1<=len(requests)<=8 or len(set(requests))!=len(requests):
        raise ValueError('SEC extraction measurement requires 1 to 8 unique explicit documents')
    requests=tuple(sec_share_preparation.request(','.join(row)) for row in requests)
    if len(set(requests))!=len(requests):raise ValueError('SEC measurement contains duplicate normalized requests')
    results=[]
    for cik,accession,name in requests:
        ciks=','.join(literal(v) for v in sorted({str(int(cik)),str(int(cik)).zfill(10)}))
        where=f'cik IN ({ciks}) AND accession_number={literal(accession)} AND document_name={literal(name)}'
        census_sql=('SELECT count() n,max(length(source_text)) bytes,uniqExact(content_sha256) hashes,'
            f'any(content_sha256) content_hash FROM q_live.sec_filing_text_v3 WHERE {where}')
        before=query(census_sql)
        if (len(before)!=1 or int(before[0]['n'])!=1 or int(before[0]['hashes'])!=1
                or not 0<int(before[0]['bytes'])<=16*1024**2):
            raise ValueError('SEC measurement source is missing, ambiguous or oversized')
        started=perf_counter()
        rows=query(f'SELECT source_text FROM q_live.sec_filing_text_v3 WHERE {where} AND length(source_text)<=16777216 LIMIT 2')
        fetch_seconds=perf_counter()-started
        if len(rows)!=1:raise ValueError('SEC measurement source changed before fetch')
        started=perf_counter()
        parsed=sec_fundamental_context.extract(rows[0]['source_text'],before[0]['content_hash'],cik)
        parse_seconds=perf_counter()-started
        if query(census_sql)!=before:raise ValueError('SEC measurement source changed during extraction')
        contexts={(r['period_start'],r['period_end'],r['unit'],r['dimensions']) for r in parsed['observations']}
        result=dict(cik=cik,accession=accession,document_name=name,source_hash=before[0]['content_hash'],
            source_bytes=int(before[0]['bytes']),payload_fetches=1,fetch_seconds=fetch_seconds,parse_seconds=parse_seconds,
            observations=len(parsed['observations']),rejections=len(parsed['rejected']),contexts=len(contexts),
            rejection_reasons=dict(sorted(Counter(row['reason'] for row in parsed['rejected']).items())),
            security_cover_observations=len(parsed['security_cover']['observations']),
            security_cover_rejections=len(parsed['security_cover']['rejected']),
            security_cover_rejection_reasons=dict(sorted(Counter(row['reason'] for row in parsed['security_cover']['rejected']).items())),
            extraction_metadata_bytes=len(json.dumps(parsed,separators=(',',':')).encode('utf-8')),
            extraction_hash=parsed['extraction_hash'])
        results.append(result)
        if progress:progress(dict(completed=len(results),total=len(requests),**result))
        del rows,parsed
    body=dict(version='retained-sec-extraction-observation-v1',documents=tuple(results),
        source_bytes=sum(r['source_bytes'] for r in results),payload_fetches=len(results),
        qualification='observed_only',upper_bound_proven=False,storage_rate_qualified=False,
        limitation='Extraction timings and serialized metadata only; not compressed CH bytes, whole-pipeline throughput or peak bounds.')
    return dict(body,evidence_hash=digest(body))
