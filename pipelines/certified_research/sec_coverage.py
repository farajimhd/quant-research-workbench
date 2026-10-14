"""Completed SEC acquisition ranges, independent of sparse fact row counts."""
from datetime import date,datetime,time,timedelta,timezone
import re

from .contracts import digest,identifier,literal
from . import schema

TABLE='sec_coverage_manifest_v3'
KINDS=('sec_daily_archive','sec_bulk_submissions','sec_bulk_companyfacts','sec_integrity_audit')
VERSION='certified-sec-acquisition-coverage-v2'


def _utc(value):
    if isinstance(value,str):value=datetime.fromisoformat(value.replace('Z','+00:00'))
    if not isinstance(value,datetime):raise ValueError('Invalid SEC coverage timestamp')
    # The manifest columns are explicitly UTC; JSONEachRow omits the suffix.
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def evaluate(rows,start,end):
    """Union completed error-free half-open intervals; never bridge a gap."""
    if type(start) is not date or type(end) is not date or start>end:
        raise ValueError('Invalid SEC acquisition range')
    first=datetime.combine(start,time(),timezone.utc)
    last=datetime.combine(end+timedelta(days=1),time(),timezone.utc)
    accepted={kind:[] for kind in KINDS};proofs=[];rejected=0;seen=set()
    for row in rows:
        key=(row['coverage_kind'],row['coverage_id'])
        if key in seen:raise ValueError('SEC coverage identity is duplicated')
        seen.add(key)
        if row['coverage_kind'] not in accepted:raise ValueError('Unexpected SEC coverage family')
        left=_utc(row['coverage_start_utc']);right=_utc(row['coverage_end_utc'])
        if left>=right:raise ValueError('SEC coverage interval is invalid')
        if (row['status'] not in ('completed','covered_empty') or int(row['error_count'])!=0
                or row['completed_at_utc'] is None):
            rejected+=1;continue
        if not re.fullmatch('[0-9a-f]{64}',row['proof_hash']):raise ValueError('SEC coverage proof is missing')
        accepted[row['coverage_kind']].append((left,right))
        proofs.append((row['coverage_kind'],row['coverage_id'],row['proof_hash']))
    gaps={}
    for kind,intervals in accepted.items():
        cursor=first;missing=[]
        for left,right in sorted(intervals):
            if right<=cursor or left>=last:continue
            if left>cursor:missing.append((cursor,min(left,last)))
            cursor=max(cursor,min(right,last))
        if cursor<last:missing.append((cursor,last))
        if missing:gaps[kind]=tuple((a.isoformat(),b.isoformat()) for a,b in missing)
    value=dict(version=VERSION,start=str(start),end=str(end),kinds=KINDS,proofs=tuple(sorted(proofs)))
    return dict(ready=not gaps,gaps=gaps,rejected_intervals=rejected,qualified_intervals=len(proofs),
        coverage_hash=digest(value),start=str(start),end=str(end))


def inventory(query,start,end,*,database='q_live',maximum_intervals=4096):
    identifier(database)
    if type(maximum_intervals) is not int or not 1<=maximum_intervals<=65536:
        raise ValueError('Invalid SEC coverage metadata bound')
    # Validate dates before composing SQL, including the exclusive upper bound.
    evaluate((),start,end)
    exists=query(f"SELECT count() n FROM system.tables WHERE database={literal(database)} AND name={literal(TABLE)}")[0]['n']
    if not int(exists):return dict(evaluate((),start,end),reason='SEC acquisition coverage manifest is missing')
    schema.verify_auxiliary_storage(query,database,(TABLE,))
    from . import sec_lineage
    snapshot=sec_lineage.qualified(query,start,end,database=database)
    rows=[dict(r,status=r['status'] if r['coverage_id'] in snapshot['qualified'] else 'unqualified_stage_lineage',
        proof_hash=snapshot['qualified'].get(r['coverage_id'],r['proof_hash']))
        for r in snapshot['parents'] if r['coverage_kind'] in KINDS]
    if len(rows)>maximum_intervals:raise ValueError('SEC coverage metadata exceeds its bound')
    return dict(evaluate(rows,start,end),unqualified_lineage=tuple(r['coverage_id'] for r in rows if r['status']=='unqualified_stage_lineage'))


def require_ready(query,start,end):
    result=inventory(query,start,end)
    if not result['ready']:raise ValueError('SEC acquisition coverage is incomplete: '+','.join(result['gaps']))
    return result
