"""Receipt-preserving FDIC backfill/forward fill over an explicit inventory."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import sys
sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import argparse
from concurrent.futures import ThreadPoolExecutor,ProcessPoolExecutor,as_completed
from datetime import datetime,timezone
import hashlib,json,platform,time
from scripts.run_fundamentals import Client,lock,save
from pipelines.fundamentals import fdic_prepare,fdic_publish,schema
from pipelines.certified_research.contracts import digest
from research.mlops.env import load_env_files
from research.mlops.clickhouse import discover_clickhouse_env_files


def run(args):
    root=Path(args.source_root).resolve();run_dir=Path(args.run_dir).resolve()
    operational=Path('D:/TradingML/runtimes').resolve()
    if not operational.is_dir() or not root.is_relative_to(operational) or not run_dir.is_relative_to(operational):
        raise ValueError('Use the available operational runtime root')
    if not 1<=args.workers<=4:raise ValueError('Use 1..4 bounded workers')
    if args.database!='arte' and not args.database.startswith('fundamentals_validation_'):raise ValueError('Unapproved database')
    if args.execute and args.database=='arte' and platform.node().upper()!='DESKTOP-SAAI85T':raise ValueError('Production is workstation-owned')
    inventory_path=(root/'inventory.json') if args.inventory is None else Path(args.inventory).resolve()
    if not inventory_path.is_relative_to(operational):raise ValueError('Inventory must be under operational runtime root')
    inventory=json.loads(inventory_path.read_text(encoding='utf-8'))
    if args.filing_ids:inventory=[r for r in inventory if r['filing_id'] in args.filing_ids]
    if not inventory:raise ValueError('Empty report inventory')
    run_dir.mkdir(parents=True,exist_ok=True)
    request=dict(inventory_hash=digest(inventory),database=args.database,execute=args.execute)
    request_file=run_dir/'request.json'
    if request_file.exists() and json.loads(request_file.read_text(encoding='utf-8'))!=request:raise ValueError('Preserve existing run request')
    save(request_file,request)
    load_env_files(discover_clickhouse_env_files(),verbose=False)
    start=time.perf_counter();results=[];active={};remaining=len(inventory)
    state=dict(state='running',phase='prepare_and_publish' if args.execute else 'prepare',total=len(inventory),
        workers=args.workers,queued=len(inventory),counts={},active=active,unit='FDIC reports',
        qualification_status='Selected bank financials; other fundamentals coverage is separate.',
        started_at=datetime.now(timezone.utc).isoformat())
    save(run_dir/'status.json',state)
    def guard():
        if (run_dir/'STOP').exists():raise ValueError('Graceful STOP requested')
    def work(report):
        guard();stamp=time.perf_counter();key=f"{report['ticker']}:{report['filing_id']}/{report['attachment_id']}"
        active[key]=dict(family=key,stage='extract_and_validate',started=time.perf_counter()-start)
        try:
            # PDF parsing uses processes: the PDF library is not thread-safe.
            prepared,receipt,cached=extraction.submit(fdic_prepare.prepare,str(root),report).result()
            if prepared['issues']:raise ValueError(json.dumps(prepared['issues']))
            if prepared.get('not_applicable_reason'):
                result=dict(state='not_applicable',rows=0,reason=prepared['not_applicable_reason'],evidence=prepared['evidence_quote'])
            elif args.execute:
                active[key]['stage']='publish_and_readback'
                client=Client(args.endpoint)
                def source_guard():
                    guard()
                    if hashlib.sha256((root/'verified'/(receipt['sha256']+'.pdf')).read_bytes()).hexdigest()!=receipt['sha256']:
                        raise ValueError('FDIC PDF changed during publication')
                try:result=fdic_publish.publish(client.query,args.database,prepared,receipt,digest(fdic_publish.ISSUERS),source_guard)
                finally:client.close()
            else:result=dict(state='prepared',rows=len(prepared['observations']))
            result=dict(result,unit=key,seconds=time.perf_counter()-stamp,prepared_cache_reused=cached)
        except Exception as exc:result=dict(state='failed',unit=key,error=str(exc),seconds=time.perf_counter()-stamp)
        finally:active.pop(key,None)
        save(run_dir/(key.replace(':','-').replace('/','-')+'.json'),result)
        return result
    with lock(operational/('fundamentals-'+digest(args.database)+'.lock')):
        if args.execute:
            client=Client(args.endpoint)
            try:schema.verify(client.query,args.database)
            finally:client.close()
        with ProcessPoolExecutor(max_workers=args.workers) as extraction, ThreadPoolExecutor(max_workers=args.workers) as pool:
            pending={pool.submit(work,r):r for r in inventory}
            for future in as_completed(pending):
                results.append(future.result());remaining-=1
                elapsed=time.perf_counter()-start
                counts={s:sum(x['state']==s for x in results) for s in ('published','reused','prepared','not_applicable','failed')}
                counts['unchanged']=counts['reused']+counts['not_applicable']
                counts['audited']=counts['prepared']
                state.update(counts=counts,queued=max(0,remaining-len(active)),active=dict(active),elapsed_seconds=elapsed,
                    elapsed=elapsed,new_rows=sum(x.get('rows',0) for x in results if x['state']=='published'),
                    reused_rows=sum(x.get('rows',0) for x in results if x['state']=='reused'),
                    selected_v1=True,samples={'fdic_report':{'published':[x['seconds'] for x in results if x['state'] in ('prepared','published')]}},
                    remaining_by_family={'fdic_report':remaining},
                    eta_seconds=elapsed/len(results)*remaining if len(results)>=3 else None,updated_at=datetime.now(timezone.utc).isoformat())
                if results[-1]['state']=='failed':state['error']=results[-1]['unit']+': '+results[-1]['error']
                save(run_dir/'status.json',state)
                filled=int(30*len(results)/len(inventory));eta='estimating' if state['eta_seconds'] is None else f"{state['eta_seconds']:.0f}s"
                print(f"[{'#'*filled}{'.'*(30-filled)}] {len(results)}/{len(inventory)} reports | facts {sum(x.get('rows',0) for x in results)} | failed {counts['failed']} | ETA {eta}",flush=True)
    state.update(state='failed' if counts['failed'] else 'complete',active={},queued=0,eta_seconds=0,results=results)
    save(run_dir/'status.json',state)
    return 1 if counts['failed'] else 0


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source-root',required=True);p.add_argument('--run-dir',required=True)
    p.add_argument('--inventory',help='New sealed inventory for forward fill; completed source receipts are shared')
    p.add_argument('--database',default='arte');p.add_argument('--endpoint',default='http://192.168.1.218:18123')
    p.add_argument('--workers',type=int,default=2);p.add_argument('--filing-ids',type=int,nargs='+')
    p.add_argument('--execute',action='store_true');a=p.parse_args()
    raise SystemExit(run(a))

