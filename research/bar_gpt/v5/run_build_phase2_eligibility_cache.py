"""Bounded read-only extraction of exact origin counts from sealed ancestors."""

import argparse
import json
import os
from pathlib import Path
import time

os.environ['PYTHONDONTWRITEBYTECODE']='1'
os.environ.setdefault('POLARS_MAX_THREADS','2')

import polars as pl
import psutil

from .phase2_ar_shards.continuation import semantic_identity
from .phase2_ar_shards.eligibility_cache import SOURCE_COLUMNS,bucket
from .phase2_ar_shards.inventory import verify_inventory
from .phase2_ar_shards.ledger import FileLedger,save_part
from .quote_repair_v1 import verify_seal
from .shards.contract import digest,file_hash,write_json


def pending_tickers(root):
    """Scope cache preparation only; omitted entries always fall open to SQL."""
    read=lambda p:json.loads(p.read_text())
    execution=read(root/'execution.json')
    assignment=read(root/'worker-assignments'/(execution['telemetry_run']+'.json'))
    verify_seal(assignment,'hash');tickers=set()
    for worker in assignment['workers']:
        try:
            status=read(root/'worker-status'/execution['telemetry_run']/f"cpu-{worker['slot']:02d}.json")
            start=worker['tickers'].index(status['ticker'])
        except (OSError,ValueError,KeyError):start=0
        tickers.update(worker['tickers'][start:])
    return tickers


def collect_counts(ledger,plan,tickers,limit,deadline):
    parts=[('checkpoint',p) for p in ledger.manifest['checkpoint'].get('deferrals',[])]
    journal=list(ledger.manifest['parts'])
    for segment in reversed(ledger.manifest.get('segments',[])):
        if len(journal)>=limit:break
        path=ledger.root/segment['name']
        if path.name!=segment['name'] or file_hash(path)!=segment['sha256']:raise ValueError('Ancestor journal segment changed')
        body=json.loads(path.read_text());verify_seal(body,'hash')
        journal=body['parts']+journal
    parts.extend(('journal',p) for p in journal[-limit:] if limit)
    counts={};inputs=[];scanned=0
    for kind,part in parts:
        if time.monotonic()>deadline:break
        frame=ledger.checked_part(part)
        if kind=='journal':
            frame=frame.filter(pl.col('kind')=='deferral').select('key',pl.col('body').alias('report'),pl.col('seal').alias('hash'))
        scanned+=len(frame)
        frame=(frame.with_columns(pl.col('report').str.json_path_match('$.unit.ticker').alias('ticker'))
               .filter(pl.col('ticker').is_in(list(tickers))))
        for row in frame.iter_rows(named=True):
            body=json.loads(row['report'])
            if digest(body)!=row['hash'] or body.get('binding')!=plan['hash']:
                raise ValueError('Ancestor deferral seal/source differs')
            unit=body['unit'];day=unit['month'];n=body.get('sampling',{}).get('session_origins')
            if len(day)!=10 or type(n) is not int or not 0<=n<4096:
                continue
            if body.get('sampling',{}).get('reason')!='session_has_fewer_than4096_origins' or body['stats']['source_origins']!=n:
                continue  # Never reinterpret a partial-prefix or unknown count.
            if row['key']!=digest([unit['ticker'],day]):raise ValueError('Ancestor session key differs')
            key=(unit['ticker'],day)
            value=dict(ticker=key[0],source_date=day,origin_count=n,report_sha256=row['hash'])
            if key in counts and counts[key]!=value:raise ValueError('Conflicting sealed full-session counts')
            counts[key]=value
        inputs.append(dict(kind=kind,**part))
    return list(counts.values()),dict(parts=inputs,reports_scanned=scanned,deadline_reached=time.monotonic()>deadline)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--parent',type=Path,required=True)
    p.add_argument('--pending-from',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--max-journal-parts',type=int,default=256)
    p.add_argument('--seconds',type=int,default=120)
    args=p.parse_args(argv)
    if not 1<=args.seconds<=300 or not 0<=args.max_journal_parts<=2048:raise ValueError('Unbounded cache preparation')
    if args.output.exists():raise ValueError('Immutable cache output already exists')
    if os.name=='nt':psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    read=lambda path:json.loads(path.read_text())
    plan=read(args.parent/'plan.json');verify_seal(plan,'hash')
    terminal=read(args.parent/'result.json')
    if terminal.get('state') not in ('complete','inventory_exhausted','capacity_reached') or terminal.get('failure') or terminal['binding']!=plan['hash']:
        raise ValueError('Cache ancestor is not verified terminal')
    verify_inventory(args.parent,plan)
    ledger=FileLedger(args.parent)
    tickers=pending_tickers(args.pending_from)
    records,evidence=collect_counts(ledger,plan,tickers,args.max_journal_parts,time.monotonic()+args.seconds)
    rows=pl.DataFrame(records) if records else pl.DataFrame(schema={'ticker':pl.String,'source_date':pl.String,'origin_count':pl.Int64,'report_sha256':pl.String})
    paths=[str(args.parent/'inventory/plan'/part['name']) for part in plan['partitions']]
    frozen=(pl.scan_parquet(paths).filter(pl.col('ticker').is_in(list(tickers)))
            .select(SOURCE_COLUMNS).join(rows.lazy(),on=['ticker','source_date'],how='inner').collect(engine='streaming'))
    if len(frozen)!=len(rows):raise ValueError('Cached count lacks exact frozen source row')
    mapping=pl.DataFrame([dict(ticker=t,bucket=bucket(t)) for t in frozen['ticker'].unique().to_list()],schema={'ticker':pl.String,'bucket':pl.String})
    frozen=frozen.join(mapping,on='ticker',how='left')
    args.output.mkdir(parents=True,exist_ok=False);parts={}
    for key,frame in frozen.partition_by('bucket',as_dict=True).items():
        frame=frame.drop('bucket').sort('ticker','source_date')
        parts[key[0]]=save_part(args.output/f'counts-{key[0]}.parquet',frame)
    body=dict(version='sealed-origin-count-cache-v1',semantic_identity=semantic_identity(plan),
        parent=dict(root=str(args.parent.resolve()),binding=plan['hash'],ledger_head_sha256=file_hash(ledger.root/'HEAD.json')),
        source_columns=list(SOURCE_COLUMNS),parts=parts,rows=len(frozen),candidate_tickers=len(tickers),
        evidence=evidence,policy='exact full-session short counts only; misses query canonical source; never prune history')
    body['hash']=digest(body);write_json(args.output/'manifest.json',body)
    print(json.dumps(dict(passed=True,rows=len(frozen),candidate_tickers=len(tickers),parts=len(parts),reports_scanned=evidence['reports_scanned'],deadline_reached=evidence['deadline_reached'])),flush=True)


if __name__=='__main__':main()
