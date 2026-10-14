"""SEC acquisition lineage: process success is not whole-pipeline coverage."""
from datetime import datetime,timezone
import json

VERSION='sec-acquisition-stage-lineage-v1'
BULK={'bulk-download','bulk-ingest','bulk-canonicalize','acceptance-submissions-enrichment'}
ARCHIVES={'daily-archive-download','validate-downloaded','filing-entity-backfill','archive-text-rebuild',
    'sec-revision-reconcile','missing-document-repair','filing-parent-reconcile','acceptance-raw-metadata-repair',
    'acceptance-archive-repair','archive-identity-repair','archive-identity-audit'}
FACTS={'bulk-download','bulk-ingest','bulk-canonicalize','xbrl-companyfacts-catchup','xbrl-integrity-repair'}
ALL=BULK|ARCHIVES|FACTS|{'sec-issuer-relationship-sync','sec-bridge-rebuild','sec-context-build','integrity-audit'}
REQUIRED={
    'sec_bulk_submissions':frozenset(BULK|{'integrity-audit'}),
    'sec_daily_archive':frozenset(ARCHIVES|{'integrity-audit'}),
    'sec_text_extraction':frozenset(ARCHIVES|{'integrity-audit'}),
    'sec_bulk_companyfacts':frozenset(FACTS|{'integrity-audit'}),
    'sec_integrity_audit':frozenset(ALL),
}
LIMIT_FLAGS=frozenset(('limit-days','limit-archives','max-filings-per-archive','limit-members','limit-ciks',
    'limit-rows','limit-parts','limit-accessions','limit-filings','max-accessions','max-documents','ciks','tickers'))


def utc(value):
    if isinstance(value,str):value=datetime.fromisoformat(value.replace('Z','+00:00'))
    if not isinstance(value,datetime):raise ValueError('Invalid SEC stage clock')
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def limited(command):
    """Reject source truncation, not batch sizes or audit sampling limits."""
    if not isinstance(command,(list,tuple)) or not command or any(not isinstance(v,str) for v in command):return True
    for index,value in enumerate(command):
        name,_,inline=value.partition('=')
        if name.removeprefix('--') not in LIMIT_FLAGS:continue
        arg=inline if '=' in value else command[index+1] if index+1<len(command) else ''
        if arg not in ('','0'):return True
        if not arg:return True
    return False


def completed_families(results):
    """Only actually executed successful prerequisite closures can finalize.

    Historical filling does not certify a live-feed observation interval.
    Resumed/skipped source stages retain their previous coverage authorities;
    they do not become new execution evidence for this run.
    """
    names=[r.stage for r in results]
    if len(names)!=len(set(names)):raise ValueError('SEC stage results are duplicated')
    done={r.stage for r in results if r.status=='ok' and r.returncode==0 and not limited(r.command)}
    positions={r.stage:i for i,r in enumerate(results)}
    return tuple(sorted(kind for kind,required in REQUIRED.items() if required<=done
        and positions['integrity-audit']>=max(positions[name] for name in required)))


def stage(row):
    """Qualify a recorded stage without trusting a sparse-data row count."""
    if (row['source']!='sec' or row['status'] not in ('completed','covered_empty')
            or int(row['error_count'])!=0 or row['completed_at_utc'] is None):return None
    metadata=json.loads(row['metadata_json']);name=metadata.get('stage')
    if (metadata.get('source')!='sec_historical_gap_fill' or name not in ALL
            or row['coverage_kind']!='sec_stage_'+name.replace('-','_') or limited(metadata.get('command'))):return None
    left=utc(row['coverage_start_utc']);right=utc(row['coverage_end_utc']);finished=utc(row['completed_at_utc'])
    if left>=right or finished<utc(row['started_at_utc']):return None
    return dict(name=name,left=left,right=right,finished=finished,id=row['coverage_id'],hash=row['proof_hash'])


def witnesses(parent,stages):
    """Exact interval union, bounded by recorded completion before the parent."""
    if parent['coverage_kind'] not in REQUIRED:return None
    if (parent['source']!='sec' or parent['status'] not in ('completed','covered_empty')
            or int(parent['error_count'])!=0 or parent['completed_at_utc'] is None):return None
    metadata=json.loads(parent['metadata_json'])
    if metadata.get('source')!='sec_historical_gap_fill':return None
    first=utc(parent['coverage_start_utc']);last=utc(parent['coverage_end_utc']);finished=utc(parent['completed_at_utc'])
    if first>=last:return None
    selected=[]
    for name in sorted(REQUIRED[parent['coverage_kind']]):
        cursor=first
        for item in sorted((s for s in stages if s['name']==name and s['finished']<=finished),key=lambda s:(s['left'],s['right'],s['id'])):
            if item['right']<=cursor or item['left']>=last:continue
            if item['left']>cursor:break
            selected.append((name,item['id'],item['hash']));cursor=min(last,item['right'])
            if cursor==last:break
        if cursor!=last:return None
    return tuple(sorted(selected))
