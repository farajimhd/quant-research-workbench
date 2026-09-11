#!/usr/bin/env python3
"""Plan, run, inspect or gracefully stop a workstation V6 daily-survivor build."""
import os
import sys
os.environ['PYTHONDONTWRITEBYTECODE']='1'
sys.dont_write_bytecode=True
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import argparse
from contextlib import contextmanager
from collections import Counter, deque
from datetime import date, datetime, timezone
from hashlib import sha256
import json
import re
import subprocess
import time
import traceback
from types import SimpleNamespace

import prototype_structure_book_clickhouse as P
from build_swing_structure_book import policy
from swing_book_paths import WORKSTATION_ENV_FILE, validate_runtime_root, ticker_directory
from swing_campaign_dashboard import Dashboard
from swing_reader_upgrade import UPGRADE_PATHS, legacy_hash_matches, transport_hash_matches, path_baselines

MAX_WORKERS=64
MAX_QUERY_THREADS=128


def validate_concurrency(workers, threads):
    if not 1<=workers<=MAX_WORKERS or not 1<=threads<=8 or workers*threads>MAX_QUERY_THREADS:
        raise ValueError('Use 1..64 workers, 1..8 threads; combined query thread budget <=128')


TRACKED=('scripts/build_swing_book_campaign.py','scripts/build_swing_structure_book.py',
 'scripts/swing_book_paths.py','src/runtime_paths.py',
 'src/market_engine/swing_book_v6.py','src/market_engine/swing_book.py',
 'src/market_engine/swing_structure.py','src/market_engine/swing_level_index.py',
 'src/market_engine/swing_book_v5.py','src/market_engine/resistance_selection.py',
 'src/backend/swing_book_source.py', *UPGRADE_PATHS)


def code_hash():
    return P.digest({p:sha256((ROOT/p).read_bytes()).hexdigest() for p in TRACKED})


def compatible_code_hash(value, hashes=None):
    hashes=dict(hashes) if hashes is not None else {p:sha256((ROOT/p).read_bytes()).hexdigest() for p in TRACKED}
    if value==P.digest(hashes):return True
    # The df562ae5 controller has identical execution semantics; only its
    # progress rendering changed. Every other pinned file must still match.
    for controller in LEGACY_DISPLAY_CONTROLLERS:
        hashes['scripts/build_swing_book_campaign.py']=controller
        if value==P.digest(hashes):return True
    return False


def reader_for_manifest(m):
    hashes={p:sha256((ROOT/p).read_bytes()).hexdigest() for p in TRACKED}
    upgrade=m.get('path_upgrade') or {}
    if upgrade.get('execution_code_hash') == P.digest(hashes):
        for old in path_baselines(hashes):
            if P.digest(old) == upgrade.get('prior_execution_code_hash'):
                return reader_for_hashes(m,old)
    return reader_for_hashes(m,hashes)


def reader_for_hashes(m, hashes):
    if compatible_code_hash(m['code_hash'], hashes):
        reader=m.get('reader','legacy')
        if reader not in ('legacy','indexed'):
            raise ValueError('Unknown campaign reader')
        return reader
    upgrade=m.get('reader_upgrade') or {}
    if ((legacy_hash_matches(m['code_hash'],hashes,campaign=True) or transport_hash_matches(m['code_hash'],hashes))
            and upgrade.get('execution_code_hash')==P.digest(hashes)
            and upgrade.get('prior_code_hash')==m['code_hash']
            and upgrade.get('reader')=='indexed'):
        return 'indexed'
    raise ValueError('Code differs from frozen plan; use explicit --upgrade-reader only for the supported V6 reader migration')


def upgrade_paths(root, m):
    hashes={p:sha256((ROOT/p).read_bytes()).hexdigest() for p in TRACKED}
    if (m.get('path_upgrade') or {}).get('execution_code_hash') != P.digest(hashes):
        for old in path_baselines(hashes):
            try:reader_for_hashes(m,old)
            except ValueError:continue
            m['path_upgrade']=dict(prior_execution_code_hash=P.digest(old),
                execution_code_hash=P.digest(hashes),requested_at=now())
            break
        else:raise ValueError('Unsupported path upgrade; pinned engine/source files must match')
    changes=[]
    for row in m['rows']:
        ticker=row['ticker']
        if row['status']=='deferred':continue
        if ticker_directory(ticker)==ticker:continue
        for key, expected, replacement in (
            ('progress_file',root/'workers'/ticker/'progress.json',root/'workers'/ticker_directory(ticker)/'progress.json'),
            ('report',root.parent/(root.name+'-v6')/ticker.lower()/'report.json',root.parent/(root.name+'-v6')/ticker_directory(ticker,lowercase=True)/'report.json')):
            old=Path(row[key])
            if old==replacement:continue
            if old!=expected:raise ValueError(f'Unexpected saved path for {ticker}: {key}')
            if old.parent.is_dir():raise ValueError(f'Existing unsafe directory needs explicit migration: {old.parent}')
            changes.append(dict(ticker=ticker,field=key,old=str(old),new=str(replacement)))
    for change in changes:
        next(r for r in m['rows'] if r['ticker']==change['ticker'])[change['field']]=change['new']
    m['path_upgrade'].setdefault('changes',[]).extend(changes)
    return changes


def record_controller_error(root, exc):
    evidence=dict(at=now(),error_type=type(exc).__name__,error=str(exc),traceback=traceback.format_exc())
    with (root/'controller-errors.jsonl').open('a',encoding='utf-8') as stream:
        stream.write(json.dumps(evidence)+'\n');stream.flush();os.fsync(stream.fileno())
    exc.campaign_error_logged=True
    return evidence


LEGACY_DISPLAY_CONTROLLERS=(
    '883be0cc9a43aa2087ff6f655b317a93dc7b33fc60d2b1e015d5524257b50e6b',
    '7993266343d422a845743c49d1a733a7d48b79d163446cdf5596c7eb5a920e02',
)


def now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def exclusive(path):
    with path.open('a+b') as lock:
        lock.seek(0);lock.write(b'0');lock.flush();lock.seek(0)
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        yield


def save(root, manifest):
    manifest['updated_at']=now()
    P.save(root/'manifest.json',manifest)


def read_json(path):
    # SMB atomic replacement may briefly deny readers. Never accept partial JSON.
    deadline=time.monotonic()+5
    while True:
        try:return json.loads(path.read_text())
        except PermissionError:
            if time.monotonic()>=deadline:raise
            time.sleep(.1)


def transport_failure(reason):
    return any(token in reason for token in ('10048','10054','10061','10053','10060',
        'RemoteDisconnected','Remote end closed','Connection reset','Connection refused','timed out'))


def duration(seconds):
    return 'unavailable' if seconds is None else f'{seconds/60:.1f} min'


def summary(m):
    counts=Counter(r['status'] for r in m['rows'])
    completed=[r for r in m['rows'] if r['status']=='completed' and r.get('elapsed_seconds') is not None]
    remaining=counts['queued']+counts['active']
    estimate=(sum(r['elapsed_seconds'] for r in completed)/len(completed)*remaining/m['workers']) if completed else None
    return dict(counts=dict(counts),total=len(m['rows']),eta_seconds=estimate,
                eta_basis='mean completed ticker time / worker count; ticker liquidity varies')


def show(m):
    s=summary(m)
    counts=' '.join(f'{k}={s["counts"].get(k,0)}' for k in ('active','queued','completed','deferred','failed','interrupted'))
    print(f'{counts} | ETA {duration(s["eta_seconds"])}',flush=True)
    for row in m['rows']:
        if row['status']=='active':
            p=Path(row['progress_file'])
            progress=json.loads(p.read_text()) if p.exists() else {}
            detail=''
            candidate=Path(row['report'])
            if progress.get('stage')=='v6' and candidate.exists():
                report=json.loads(candidate.read_text());profiles=report.get('session_profiles',[])
                elapsed=sum(x['total_seconds'] for x in profiles)
                estimate=elapsed/len(profiles)*max(0,row['days']-len(profiles)) if profiles else None
                detail=f' days={len(profiles)}/{row["days"]} stage ETA={duration(estimate)}'
            print(f'  {row["ticker"]}: {progress.get("stage","starting")}{detail} | elapsed {duration(time.time()-row["started_epoch"])}',flush=True)


def plan(args):
    root=args.runtime
    if (root/'manifest.json').exists():raise ValueError('Plan exists; use run to resume or choose a new runtime')
    client=P.Client(args.env_file,args.threads)
    policy(client,'structure_book_campaign_preflight')
    stamp=client.query('SELECT toString(max(universe_date)) day FROM q_live.feature_tradable_universe_v1 FINAL','universe_date')[0]['day']
    # Freeze the current published tradable membership; do not infer broker identity.
    universe=[];after=''
    while True:
        page=client.query(f"SELECT ticker,symbol_id,listing_id,security_id,ibkr_conid,massive_ticker,source_run_id FROM q_live.feature_tradable_universe_v1 FINAL WHERE universe_date={P.literal(stamp)} AND is_tradable=1 AND ticker>{P.literal(after)} ORDER BY ticker,symbol_id LIMIT 500",'universe_page')
        if not page:break
        # Read a complete final ticker group before advancing the cursor.
        last=page[-1]['ticker']
        page=[r for r in page if r['ticker']!=last]+client.query(f"SELECT ticker,symbol_id,listing_id,security_id,ibkr_conid,massive_ticker,source_run_id FROM q_live.feature_tradable_universe_v1 FINAL WHERE universe_date={P.literal(stamp)} AND is_tradable=1 AND ticker={P.literal(last)}",'universe_boundary')
        universe.extend(page);after=last
    if not universe:raise ValueError('Published tradable universe is empty')
    rows=[]
    grouped={}
    for u in universe:grouped.setdefault(u['ticker'],[]).append(u)
    if args.tickers:
        if set(args.tickers)-grouped.keys():raise ValueError('Requested ticker is not in the published tradable universe')
        grouped={k:v for k,v in grouped.items() if k in args.tickers}
    for offset in range(0,len(grouped),500):
        names=sorted(grouped)[offset:offset+500]
        coverage=client.query(f"SELECT ticker,count() days,sum(event_count) events,max(source_date) last_day FROM market_sip_compact.events_ordinal_continuity FINAL WHERE ticker IN ({','.join(map(P.literal,names))}) AND source_date BETWEEN '{args.start}' AND '{args.end}' GROUP BY ticker",'coverage')
        by_ticker={r['ticker']:r for r in coverage}
        for ticker in names:
            u=grouped[ticker];c=by_ticker.get(ticker,{})
            reason=''
            if len(u)!=1:reason='ambiguous published ticker identity'
            elif not re.fullmatch(r'[A-Z][A-Z0-9.-]{0,19}',ticker):reason='unsupported canonical ticker syntax'
            elif u[0].get('massive_ticker') not in (None,'',ticker):reason='published market ticker differs; identity review required'
            elif not c:reason='no certified canonical history in requested range'
            report=root.parent/(root.name+'-v6')/(ticker_directory(ticker,lowercase=True) if not reason else ticker.lower())/'report.json'
            rows.append(dict(ticker=ticker,status='deferred' if reason else 'queued',reason=reason,
                days=int(c.get('days',0)),events=int(c.get('events',0)),
                report=str(report),progress_file=str(root/'workers'/(ticker_directory(ticker) if not reason else ticker)/'progress.json')))
    # Long jobs first reduces the final tail while keeping each ticker ordered.
    rows.sort(key=lambda r:(-r['events'],r['ticker']))
    m=dict(schema_version=2,book_version='causal-swing-closing-book-6',created_at=now(),code_hash=code_hash(),universe_date=stamp,
        universe_hash=P.digest(universe),universe=universe,start=args.start,end=args.end,
        workers=args.workers,threads=args.threads,rows=rows,reader='indexed')
    save(root,m);P.save(root/'planning-profiles.json',client.profiles)
    print(f'Frozen universe {stamp}: {len(rows)} tickers',flush=True)
    show(m)


def worker(args):
    from research.mlops.env import load_env_files
    load_env_files([args.env_file],verbose=False)
    import build_swing_structure_book as builder
    m=read_json(args.runtime/'manifest.json')
    if m.get('schema_version')!=2 or m.get('book_version')!='causal-swing-closing-book-6':raise ValueError('Not a V6 campaign plan')
    reader=reader_for_manifest(m)
    row=next(r for r in m['rows'] if r['ticker']==args.ticker)
    progress=Path(row['progress_file']);progress.parent.mkdir(parents=True,exist_ok=True)
    started=time.perf_counter()
    def publish(stage,**values):P.save(progress,dict(ticker=args.ticker,stage=stage,updated_at=now(),**values))
    options=SimpleNamespace(start=m['start'],end=m['end'],threads=m['threads'],env_file=args.env_file,
        stop_file=args.runtime/'STOP',reader=reader)
    try:
        options.survivor_only=True
        options.runtime=args.runtime.parent/(args.runtime.name+'-v6')
        publish('v6')
        builder.run(args.ticker,options)
        report=json.loads(Path(row['report']).read_text())
        proof=json.loads(Path(row['report']).with_name('validation.json').read_text())
        if proof['status']!='passed' or proof['database']!=report['database']:raise ValueError('V6 validation missing')
        publish('completed',database=report['database'],elapsed_seconds=time.perf_counter()-started,report=row['report'])
        return 0
    except KeyboardInterrupt:
        publish('interrupted',elapsed_seconds=time.perf_counter()-started);return 130
    except Exception as exc:
        publish('failed',error=str(exc),elapsed_seconds=time.perf_counter()-started)
        raise


def run(args):
    root=args.runtime;m=read_json(root/'manifest.json')
    workers=getattr(args,'workers',None)
    threads=getattr(args,'threads',None)
    if workers is not None:m['workers']=workers
    if threads is not None:m['threads']=threads
    validate_concurrency(m['workers'],m['threads'])
    if m.get('schema_version')!=2 or m.get('book_version')!='causal-swing-closing-book-6':raise ValueError('Not a V6 campaign plan')
    upgrading=getattr(args,'upgrade_reader',False) or getattr(args,'upgrade_transport',False)
    if upgrading:
        hashes={p:sha256((ROOT/p).read_bytes()).hexdigest() for p in TRACKED}
        if not (legacy_hash_matches(m['code_hash'],hashes,campaign=True) or transport_hash_matches(m['code_hash'],hashes) or m['code_hash']==code_hash()):
            raise ValueError('Unsupported reader migration; frozen engine/source files must match')
        m['reader_upgrade']=dict(prior_code_hash=m['code_hash'],execution_code_hash=code_hash(),
            reader='indexed',requested_at=now(),verification='required_per_worker_before_new_sessions')
    if getattr(args,'upgrade_paths',False):upgrade_paths(root,m)
    reader_for_manifest(m)
    if P.digest(m['universe'])!=m['universe_hash']:raise ValueError('Frozen universe hash mismatch')
    # A crashed controller may leave a worker finishing its session. Never
    # reset its progress or start a duplicate writer while it still owns a lock.
    for row in m['rows']:
        lock=Path(row['progress_file']).parent/'worker.lock'
        if lock.exists():
            with exclusive(lock):pass
    if upgrading:
        save(root,m)
        print('Indexed reader upgrade requested: each worker must pass candle and checkpoint parity before new session writes.',flush=True)
    (root/'STOP').unlink(missing_ok=True)
    retried=0
    for row in m['rows']:
        if row['status'] in ('active','interrupted') or args.retry_failed and row['status']=='failed':
            row['previous_attempt']={k:row[k] for k in ('status','reason','exit_code','elapsed_seconds') if k in row}
            row['retry_count']=row.get('retry_count',0)+1
            row['status']='queued';retried+=1
    m.pop('stop_reason',None)
    save(root,m)
    print(f'Resume: workers={m["workers"]}, threads/worker={m["threads"]}, retried tickers={retried}; completed books retained.',flush=True)
    active={};last=0.;stopping=False;network_failures=deque()
    dashboard=Dashboard(m)
    try:
        dashboard.start()
        while active or any(r['status']=='queued' for r in m['rows']):
            stopping=stopping or (root/'STOP').exists()
            while not stopping and len(active)<m['workers']:
                row=next((r for r in m['rows'] if r['status']=='queued'),None)
                if row is None:break
                folder=Path(row['progress_file']).parent;folder.mkdir(parents=True,exist_ok=True)
                Path(row['progress_file']).unlink(missing_ok=True)
                log=(folder/'worker.log').open('a',encoding='utf-8')
                command=[sys.executable,'-B',str(Path(__file__).resolve()),'worker','--runtime',str(root),'--ticker',row['ticker'],'--env-file',str(args.env_file)]
                try:
                    process=subprocess.Popen(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
                except BaseException:
                    log.close()
                    raise
                row.update(status='active',started_epoch=time.time(),pid=process.pid)
                active[process.pid]=(process,row,log);save(root,m)
                dashboard.bind(m)
            for pid,(process,row,log) in list(active.items()):
                result=process.poll()
                if result is None:continue
                log.close();p=Path(row['progress_file'])
                progress=read_json(p) if p.exists() else {}
                status='completed' if result==0 and progress.get('stage')=='completed' else 'interrupted' if result==130 else 'failed'
                row.update(status=status,elapsed_seconds=time.time()-row['started_epoch'],exit_code=result,
                    reason=progress.get('error','' if status=='completed' else 'See worker.log'),database=progress.get('database'))
                active.pop(pid);save(root,m)
                if status=='failed' and transport_failure(row['reason']):
                    stamp=time.monotonic()
                    network_failures.append(stamp)
                    while network_failures and stamp-network_failures[0]>180:
                        network_failures.popleft()
                    if len(network_failures)>=4 and not stopping:
                        stopping=True
                        m['stop_reason']='Four workers exhausted transport recovery within 180 seconds; stopped dispatch to protect remaining tickers. Check connectivity before resuming.'
                        (root/'STOP').touch()
                        dashboard.event(m['stop_reason'])
                dashboard.event(f'{row["ticker"]}: {status} | {duration(row["elapsed_seconds"])} | {row["reason"]}')
            if time.monotonic()-last>=args.progress_seconds:
                # State transitions already persist the manifest. A display
                # heartbeat must not rewrite the frozen universe every second.
                dashboard.update(m,stopping);last=time.monotonic()
            if stopping and not active:break
            try:time.sleep(.5)
            except KeyboardInterrupt:
                (root/'STOP').touch();stopping=True
                dashboard.event('Stopping at session boundaries; waiting for workers to checkpoint.')
    except KeyboardInterrupt:
        m['stop_reason']='Operator interrupt; active workers checkpointed before controller exit.'
        raise
    except Exception as exc:
        m['stop_reason']=f'Controller error: {type(exc).__name__}: {exc}'
        m['controller_error']=record_controller_error(root,exc)
        raise
    finally:
        if active:
            (root/'STOP').touch()
            for process,row,log in active.values():process.wait();log.close();row['status']='interrupted'
        try:save(root,m)
        finally:dashboard.stop(m)
    return 1 if any(r['status']=='failed' for r in m['rows']) else 130 if stopping else 2 if any(r['status']=='deferred' for r in m['rows']) else 0


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=('plan','run','status','stop','worker'))
    p.add_argument('--runtime',type=Path,required=True)
    p.add_argument('--start',default='2025-01-01');p.add_argument('--end',default=date.today().isoformat())
    p.add_argument('--workers',type=int,default=None,help='plan default 4; run overrides saved concurrency')
    p.add_argument('--threads',type=int,default=None,help='plan default 2; run overrides saved query threads')
    p.add_argument('--progress-seconds',type=int,default=1);p.add_argument('--retry-failed',action='store_true')
    p.add_argument('--upgrade-reader',action='store_true',help='explicitly migrate a stopped legacy V6 campaign; verify reference candles and saved state before new writes')
    p.add_argument('--upgrade-transport',action='store_true',help='accept the exact supported transport revision; reverify reader and checkpoint parity')
    p.add_argument('--upgrade-paths',action='store_true',help='migrate known prior code and reserved Windows ticker paths; preserve checkpoints')
    p.add_argument('--ticker')
    p.add_argument('--tickers',nargs='+',help='Explicit subset for a pilot; omitted means every published tradable ticker')
    p.add_argument('--env-file',type=Path,default=WORKSTATION_ENV_FILE)
    return p


def main():
    p=parser()
    args=p.parse_args()
    if (args.upgrade_reader or args.upgrade_transport or args.upgrade_paths) and args.action!='run':p.error('Upgrade flags apply only to run')
    if args.action=='plan':
        args.workers=4 if args.workers is None else args.workers
        args.threads=2 if args.threads is None else args.threads
    try:args.runtime=validate_runtime_root(args.runtime)
    except ValueError as exc:p.error(str(exc))
    try:validate_concurrency(args.workers if args.workers is not None else 4,args.threads if args.threads is not None else 2)
    except ValueError as exc:p.error(str(exc))
    if args.progress_seconds<1 or date.fromisoformat(args.start)>date.fromisoformat(args.end):p.error('Invalid dates or progress interval')
    args.runtime.mkdir(parents=True,exist_ok=True)
    if args.action=='status':show(json.loads((args.runtime/'manifest.json').read_text()));return 0
    if args.action=='stop':(args.runtime/'STOP').touch();print('Stop requested; workers finish their current session.');return 0
    if args.action=='worker':
        if not args.ticker or not re.fullmatch(r'[A-Z][A-Z0-9.-]{0,19}',args.ticker):p.error('Invalid worker ticker')
        folder=args.runtime/'workers'/ticker_directory(args.ticker);folder.mkdir(parents=True,exist_ok=True)
        with exclusive(folder/'worker.lock'):return worker(args)
    # OS lock survives neither a crash nor a reboot; no stale PID guessing.
    with exclusive(args.runtime/'controller.lock'):
        try:
            if args.action=='plan':plan(args);return 0
            return run(args)
        except Exception as exc:
            if not getattr(exc,'campaign_error_logged',False):record_controller_error(args.runtime,exc)
            raise


if __name__=='__main__':
    try:raise SystemExit(main())
    except KeyboardInterrupt:raise SystemExit(130)
    except Exception as exc:print(f'Campaign failed: {exc}',file=sys.stderr);raise SystemExit(1)
