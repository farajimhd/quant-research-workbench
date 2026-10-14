"""Read-only bounded retained SEC extraction measurements, never pilot qualification."""
import json
from collections import Counter
from time import perf_counter

from . import sec_share_preparation,sec_fundamental_context,sec_fundamental_reconciliation,facts
from . import sec_precision
from .contracts import digest,literal


def measure(query,requests,*,progress=None,compare_bulk=False):
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
        if compare_bulk:
            precision_report=sec_precision.report(parsed,include_projections=True);precision=precision_report['inventory']
            result['original_precision_inventory']={k:precision[k] for k in ('inventory_hash','observations','semantic_groups',
                'duplicate_groups','dimensioned_groups','rejected_numeric_facts','mode_status_counts','certified')}
            result['original_report_precision_gates']={mode:dict(gate_hash=value['gate_hash'],ready=value['ready'],
                failures=len(value['failures']),rejected_numeric_facts=value['rejected_numeric_facts'],certified=False)
                for mode,value in precision_report['gates'].items()}
            result['original_reported_projection_candidates']={mode:(dict(projection_hash=value['projection_hash'],
                groups=len(value['groups']),observations=value['observations'],inferred_values=False,certified=False) if value else None)
                for mode,value in precision_report['projections'].items()}
            tags=tuple(sorted({tag for metric in facts.catalog() for tag in metric.tags}))
            bulk=sec_fundamental_reconciliation.load_bulk(query,cik,accession,tags)
            comparison=sec_fundamental_reconciliation.compare_original(parsed,bulk)
            after=sec_fundamental_reconciliation.load_bulk(query,cik,accession,tags)
            if digest(sorted(bulk,key=lambda r:r['source_record']))!=digest(sorted(after,key=lambda r:r['source_record'])):
                raise ValueError('SEC bulk source changed during read-only comparison')
            result.update(bulk_comparisons=len(bulk),bulk_numeric_types=dict(sorted(Counter(r['source_numeric_type'] for r in bulk).items())),
                comparison_status_counts=dict(sorted(Counter(r['status'] for r in comparison['comparisons']).items())),
                unmatched_original_facts=len(comparison['unmatched_original_positions']),comparison_hash=comparison['comparison_hash'],
                public_time_qualified=False)
            missing=set(comparison['unmatched_original_positions'])
            result['unmatched_original_context_counts']=dict(sorted(Counter('dimensioned' if row['dimensions'] else 'unscoped'
                for row in parsed['observations'] if row['source_position'] in missing).items()))
            by_position={row['source_position']:row for row in parsed['observations']}
            conflicts=[row for row in comparison['comparisons'] if row['status']=='original_value_conflict']
            result['conflict_examples']=tuple(dict(source_record=row['source_record'],source_value=row['source_value'],
                originals=tuple(dict(source_position=p,value=by_position[p]['value'],decimals=by_position[p]['decimals'],
                    scale=by_position[p]['scale'],context_id=by_position[p]['context_id']) for p in row['original_positions'][:8]),
                original_count=len(row['original_positions']),original_examples_truncated=len(row['original_positions'])>8)
                for row in conflicts[:8])
            result['conflict_examples_truncated']=len(conflicts)>8
            assessments=[]
            for conflict in conflicts[:8]:
                witnesses=tuple(by_position[p] for p in conflict['original_positions'])
                modes={}
                for mode in ('round-to-nearest','truncation'):
                    try:modes[mode]=sec_precision.assess(witnesses,mode=mode)
                    except ValueError as error:modes[mode]=dict(status='unqualified_precision',reason=str(error),certified=False)
                assessments.append(dict(source_record=conflict['source_record'],modes=modes))
            result['precision_assessments']=tuple(assessments)
        if len(json.dumps(result,separators=(',',':')).encode('utf-8'))>1024*1024:
            raise ValueError('SEC diagnostic measurement metadata exceeds 1MiB per document')
        results.append(result)
        if progress:progress(dict(completed=len(results),total=len(requests),**result))
        del rows,parsed
    body=dict(version='retained-sec-extraction-observation-v1',documents=tuple(results),
        source_bytes=sum(r['source_bytes'] for r in results),payload_fetches=len(results),
        qualification='observed_only',upper_bound_proven=False,storage_rate_qualified=False,
        limitation='Extraction timings and serialized metadata only; not compressed CH bytes, whole-pipeline throughput or peak bounds.')
    return dict(body,evidence_hash=digest(body))
