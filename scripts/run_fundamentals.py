"""Backfill and forward-fill share this bounded, receipt-driven command."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import sys
sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import argparse
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from contextlib import contextmanager
from datetime import date, datetime, timezone,timedelta
import json
import threading
import time
import platform
import shutil
from uuid import uuid4
from pipelines.certified_research.contracts import digest
from pipelines.fundamentals import schema
from pipelines.fundamentals.producer import plan, publish
from pipelines.fundamentals.progress import render
from research.mlops.clickhouse import (ClickHouseHttpClient, default_clickhouse_user,
    default_clickhouse_password, discover_clickhouse_env_files)
from research.mlops.env import load_env_files


class Client:
    def __init__(self, endpoint, threads=1, memory_gib=2):
        self.http=ClickHouseHttpClient(endpoint,default_clickhouse_user(),default_clickhouse_password(),
            timeout_seconds=310,persistent=True,max_persistent_idle_seconds=10,
            default_query_params={'max_threads':threads,'max_memory_usage':memory_gib*1024**3,
                'max_execution_time':300,'max_result_rows':100000,'result_overflow_mode':'throw',
                'max_result_bytes':128*1024**2,'max_query_size':4*1024**2,'wait_end_of_query':1})

    def query(self, sql, *, read=True):
        query_id='fundamentals-'+uuid4().hex
        try:
            text=self.http.execute(sql+(' FORMAT JSONEachRow' if read else ''),query_id=query_id)
            return [json.loads(line) for line in text.splitlines() if line.strip()] if read else []
        except BaseException:
            # HTTP failure may leave a server query alive. Drain only this owned
            # query; never automatically replay an ambiguous write.
            self.http.close()
            try:
                self.http.execute(f"KILL QUERY WHERE query_id='{query_id}' SYNC")
            except Exception:
                pass
            raise

    def close(self):
        self.http.close()


@contextmanager
def lock(path):
    import msvcrt
    with path.open('a+b') as stream:
        if stream.tell()==0:
            stream.write(b'0');stream.flush()
        stream.seek(0)
        try:
            msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:
            raise ValueError('Another fundamentals controller owns this endpoint/database') from None
        try:
            yield
        finally:
            stream.seek(0);msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)


def save(path, value):
    tmp=path.with_name(path.name+'.'+uuid4().hex+'.tmp')
    try:
        tmp.write_text(json.dumps(value,indent=2,default=str),encoding='utf-8')
        for attempt in range(21):
            try:
                tmp.replace(path)
                return
            except PermissionError:
                # Windows readers may briefly hold a non-delete-sharing handle.
                # Preserve the old complete JSON until an atomic replace succeeds.
                if attempt==20:raise
                time.sleep(.1)
    finally:
        tmp.unlink(missing_ok=True)


def run(args):
    runtime=Path('D:/TradingML/runtimes').resolve()
    root=Path(args.run_dir).resolve()
    if not runtime.is_dir() or not root.is_relative_to(runtime):
        raise ValueError('Use the available D:/TradingML/runtimes operational root')
    if not 1<=args.workers<=8:
        raise ValueError('Workers must be between 1 and 8; each uses 1 thread and 2 GiB')
    if args.database!='arte' and not args.database.startswith('fundamentals_validation_'):
        raise ValueError('Use arte or an explicitly isolated fundamentals_validation_ database')
    if args.execute and args.database=='arte' and platform.node().upper()!='DESKTOP-SAAI85T':
        raise ValueError('Production publication is workstation-owned; deploy committed code before executing there')
    manifest=json.loads(Path(args.population).read_text(encoding='utf-8-sig'))
    if set(manifest)!={'families','provenance','captured_at'} or not manifest['provenance'] or not manifest['captured_at']:
        raise ValueError('Population must contain families, provenance and captured_at')
    selected=getattr(args,'selected_v1',False)
    if selected:
        from pipelines.fundamentals.v1 import FAMILIES,contract_hash
        manifest=dict(manifest,families={k:v for k,v in manifest['families'].items() if k in FAMILIES})
    root.mkdir(parents=True,exist_ok=True)
    load_env_files(discover_clickhouse_env_files(),verbose=False)
    owner=Client(args.endpoint)
    try:
        with lock(runtime/('fundamentals-'+digest(args.database)+'.lock')):
            if (root/'STOP').exists():
                raise ValueError('STOP is present; preserve it until an explicit resume')
            since=None;until=None
            if selected and args.mode=='forward-fill':
                since=getattr(args,'since_recorded',None)
                if since is None and (root/'watermark.json').exists():
                    watermark=json.loads((root/'watermark.json').read_text())
                    if watermark.get('feature_contract')!=contract_hash():raise ValueError('Forward feature contract changed; explicit migration required')
                    since=datetime.fromisoformat(watermark['through'])-timedelta(days=7)
                if since is None:raise ValueError('First forward fill requires --since-recorded with an aware timestamp')
                if since.tzinfo is None:raise ValueError('Forward watermark must be timezone-aware')
                until=datetime.now(timezone.utc)
                if since>=until:raise ValueError('Forward watermark must precede now')
            batches=plan(owner.query,manifest['families'],args.start,args.end,args.cohort_size,
                cache_dir=root,progress=lambda f:print(f'Planning {f}: checking occupied source partitions',flush=True),selected=selected,
                observed_since=since,observed_until=until)
            profile_count=getattr(args,'profile_batches_per_family',0)
            if profile_count:
                if args.database=='arte' or not 1<=profile_count<=3:
                    raise ValueError('Bounded profiling requires an isolated validation database and 1..3 batches per family')
                sampled=[]
                for family in manifest['families']:
                    family_batches=[b for b in batches if b.family==family]
                    if family_batches:
                        indexes=({0} if profile_count==1 else
                            {round(i*(len(family_batches)-1)/(profile_count-1)) for i in range(profile_count)})
                        sampled.extend(family_batches[i] for i in sorted(indexes))
                batches=sampled
            request={'population':manifest,'start':str(args.start),'end':str(args.end),
                'database':args.database,'endpoint':args.endpoint,'cohort_size':args.cohort_size}
            if profile_count:request['profile_batches_per_family']=profile_count
            if selected:request.update(feature_contract=contract_hash(),mode=args.mode)
            previous=root/'request.json'
            if previous.exists() and json.loads(previous.read_text())!=request:
                raise ValueError('Existing request differs; use a separate run directory, retaining shared receipts')
            if not args.execute:
                save(root/'plan.json',{'request':request,'batches':len(batches),
                    'families':{f:sum(b.family==f for b in batches) for f in manifest['families']},
                    'population_provenance':manifest['provenance']})
                print(f"Plan: {len(batches):,} source-partition/cohort batches; "
                    f"{len(manifest['families'])} families. No database writes or acquisition.")
                return 0
            save(previous,request)
            if until:save(root/'window.json',dict(since=since.isoformat(),until=until.isoformat(),overlap_days=7))
            schema.install(owner.query,args.database)
            state={'state':'running','mode':args.mode,'counts':dict(published=0,unchanged=0,audited=0,failed=0),
                'active':{},'queued':len(batches),'elapsed':0,'error':None,
                'total':len(batches),'workers':args.workers,'new_rows':0,'reused_rows':0,'samples':{},
                'exclusions':len(manifest['provenance'].get('exclusions',[])),
                'remaining_by_family':{f:sum(b.family==f for b in batches) for f in manifest['families']}}
            if selected:
                state['selected_v1']=True
                state['qualification_status']='Selected v1: structured normalization; public/recorded clocks explicit. Gap coverage reported separately.'
            timing_profile=getattr(args,'timing_profile',None)
            if timing_profile:
                profile_root=Path(timing_profile).resolve()
                if not profile_root.is_relative_to(runtime):raise ValueError('Timing profile must be in runtime root')
                profile_request=json.loads((profile_root/'request.json').read_text())
                for key in ('population','start','end','cohort_size','endpoint'):
                    if profile_request.get(key)!=request[key]:raise ValueError('Timing profile scope differs')
                profile_path=profile_root/'batches.jsonl'
                if profile_path.stat().st_size>4*1024**2:raise ValueError('Timing profile exceeds bounded pilot size')
                for line in profile_path.read_text().splitlines():
                    result=json.loads(line)
                    if result['state']=='published':
                        state['samples'].setdefault(result['family'],{}).setdefault('published',[]).append(result['seconds'])
            started=time.monotonic(); mutex=threading.Lock();last_snapshot=[0.0]
            def snapshot(force=False):
                if not force and time.monotonic()-last_snapshot[0]<10:return
                with mutex:
                    state['elapsed']=time.monotonic()-started
                    state['updated_at']=datetime.now(timezone.utc).isoformat()
                    save(root/'status.json',state)
                    print(render(state,shutil.get_terminal_size((80,24)).columns),flush=True)
                    last_snapshot[0]=time.monotonic()
            def worker(batch):
                client=Client(args.endpoint)
                unit_started=time.monotonic()
                def progress(stage):
                    with mutex:
                        state['active'][batch.key]={'family':batch.family,'stage':stage,'started':unit_started-started}
                try:
                    result=publish(client.query,batch,args.database,audit=args.audit,progress=progress)
                    with mutex:
                        state['counts'][result['state']]+=1
                        state['remaining_by_family'][batch.family]-=1
                        state['reused_rows' if result['state']=='unchanged' else 'new_rows']+=result['rows'] if result['state']!='audited' else 0
                        samples=state['samples'].setdefault(batch.family,{}).setdefault(result['state'],[])
                        samples.append(time.monotonic()-unit_started)
                        del samples[:-32]
                        with (root/'batches.jsonl').open('a',encoding='utf-8') as f:
                            f.write(json.dumps({'batch':batch.key,'family':batch.family,**result})+'\n')
                except Exception as exc:
                    with mutex:
                        state['counts']['failed']+=1
                        state['error']=f'{batch.family}: {str(exc)[:2000]}'
                        (root/'STOP').write_text('Failed batch; diagnose before explicitly resuming.\n')
                    raise
                finally:
                    with mutex:
                        state['active'].pop(batch.key,None)
                    client.close()
            pending={};iterator=iter(batches);draining=False
            snapshot(force=True)
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                while True:
                    if (root/'STOP').exists():draining=True
                    while not draining and len(pending)<args.workers:
                        batch=next(iterator,None)
                        if batch is None:break
                        with mutex:state['queued']-=1
                        pending[pool.submit(worker,batch)]=batch
                    if not pending:break
                    try:
                        done,_=wait(pending,timeout=10,return_when=FIRST_COMPLETED)
                    except KeyboardInterrupt:
                        (root/'STOP').write_text('User interrupted; active batches drain before exit.\n')
                        draining=True;continue
                    for future in done:
                        pending.pop(future)
                        try:future.result()
                        except Exception:draining=True
                    snapshot(force=draining)
            state['state']='failed' if state['counts']['failed'] else 'stopped' if draining else 'complete'
            schema.verify(owner.query,args.database)
            if state['state']=='complete' and until:save(root/'watermark.json',dict(through=until.isoformat(),feature_contract=contract_hash()))
            snapshot(force=True)
            return 1 if state['state']=='failed' else 130 if draining else 0
    finally:
        owner.close()


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode',choices=('backfill','forward-fill'))
    p.add_argument('--population',required=True,help='Reviewed family-to-entity-ID JSON manifest; no ticker guesses')
    p.add_argument('--run-dir',default='D:/TradingML/runtimes/fundamentals-retained-v1')
    p.add_argument('--endpoint',default='http://192.168.1.218:18123')
    p.add_argument('--database',default='arte')
    p.add_argument('--start',type=date.fromisoformat,default=date(1900,1,1))
    p.add_argument('--end',type=date.fromisoformat,default=date(2140,1,1))
    p.add_argument('--workers',type=int,default=4)
    p.add_argument('--cohort-size',type=int,default=128)
    p.add_argument('--execute',action='store_true',help='Publish retained sources; default is read-only planning')
    p.add_argument('--audit',action='store_true',help='Explicit exact source/output audit, including completed batches')
    p.add_argument('--selected-v1',action='store_true',help='Selected v1 features; preserve existing records and use merge-neutral incremental receipts')
    p.add_argument('--since-recorded',type=datetime.fromisoformat,help='First selected forward-fill observation watermark; subsequent runs use saved watermark with seven-day overlap')
    p.add_argument('--profile-batches-per-family',type=int,default=0,
        help='Isolated validation only: sample 1..3 source partitions per family before a full run')
    p.add_argument('--timing-profile',help='Matching isolated pilot runtime; seeds approximate per-family ETA only')
    args=p.parse_args(argv)
    try:return run(args)
    except KeyboardInterrupt:
        root=Path(args.run_dir).resolve();runtime=Path('D:/TradingML/runtimes').resolve()
        if root.is_relative_to(runtime) and root.is_dir():
            (root/'STOP').write_text('User interrupted; do not resume without explicit action.\n')
            if (root/'status.json').exists():
                state=json.loads((root/'status.json').read_text())
                state['state']='stopped';state['active']={};save(root/'status.json',state)
        print('Fundamentals stopped; completed receipts preserved.',file=sys.stderr)
        return 130
    except Exception as exc:
        print(f'Fundamentals blocked: {exc}',file=sys.stderr);return 1


if __name__=='__main__':
    raise SystemExit(main())
