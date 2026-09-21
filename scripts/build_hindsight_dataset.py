"""Resumable multi-session Phase 1 -> greedy Phase 2 campaign and benchmark.

Examples:
  python -B scripts/build_hindsight_dataset.py run --start 2026-08-21 --end 2026-08-21
  python -B scripts/build_hindsight_dataset.py benchmark --date 2026-08-21
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
os.environ.setdefault('POLARS_MAX_THREADS','2')
import sys
sys.dont_write_bytecode=True
from pathlib import Path
REPO=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(REPO))
import argparse
from concurrent.futures import ThreadPoolExecutor,wait,FIRST_COMPLETED
from datetime import date,datetime,time,timezone
import json
import signal
import subprocess
import threading
from time import monotonic
from urllib.request import urlopen
from hashlib import sha256

import psutil
from rich.console import Console
from rich.live import Live
from rich.table import Table
from src.data_provider.calendar import market_sessions
from src.runtime_paths import runtime_root,WORKSTATION_NAME
from src.market_engine.hindsight_phase1 import digest,NY
from src.market_engine.hindsight_phase1_source import client,query,universe_sql,validate_universe
from src.market_engine.hindsight_batch import worker_budget
from src.market_engine.level_book_store import read,write
from src.backend.qmd_gateway_client import qmd_history_base_url
from research.mlops.env import load_env_files
from research.mlops.clickhouse import discover_clickhouse_env_files
from scripts.build_hindsight_phase1 import exclusive

SOURCES=('scripts/build_hindsight_dataset.py','scripts/build_hindsight_phase1.py',
         'scripts/build_hindsight_greedy.py','src/market_engine/hindsight_phase1.py',
         'src/market_engine/hindsight_phase1_source.py','src/market_engine/hindsight_greedy.py',
         'src/market_engine/hindsight_batch.py','src/backend/hindsight_service.py',
         'src/market_engine/hindsight.py','src/backend/qmd_gateway_client.py')
STOP=threading.Event()


def preflight(args):
    # On workstation use its local alias; never route artifact writes over SMB.
    if os.environ.get('COMPUTERNAME','').upper()==WORKSTATION_NAME and not os.environ.get('QW_RUNTIME_ROOT'):
        os.environ['QW_RUNTIME_ROOT']=r'D:\TradingML\runtimes'
    root=runtime_root().resolve()
    if not root.is_dir():raise ValueError('Required runtime root unavailable: '+str(root))
    load_env_files(discover_clickhouse_env_files(),verbose=False)
    if args.qmd_url:os.environ['QMD_HISTORY_GATEWAY_URL']=args.qmd_url
    endpoint=qmd_history_base_url()
    with urlopen(endpoint.rstrip('/')+'/health',timeout=15) as response:health=json.load(response)
    if health.get('status')!='ready' or not health.get('source_fingerprint'):
        raise ValueError('QMD History must be ready with source fingerprint')
    c=client(1)
    try:query(c,'SELECT 1 AS ready')
    finally:c.close()
    workers=worker_budget(args.workers,threads=max(2,args.macd_readers))
    if args.day_workers>workers:raise ValueError('day-workers cannot exceed total workers')
    return root,endpoint,health['source_fingerprint'],workers


def table(counts,active,total,seconds):
    t=Table(title='Hindsight dataset | Phase 1 -> Phase 2',expand=True)
    for label in ('Done','Active','Queued','Failed','Elapsed'):t.add_column(label)
    t.add_row(str(counts['completed']),str(len(active)),str(total-sum(counts.values())-len(active)),str(counts['failed']),f'{seconds:.0f}s')
    t.caption=' | '.join(f'{day}: {value}' for day,value in sorted(active.items())) or 'No active sessions'
    return t


def stop_requested(root):return STOP.is_set() or (root/'STOP').exists()


def count_label(counts):
    return f"done {counts.get('completed',0)}, reused {counts.get('reused',0)}, failed {counts.get('failed',0)}"


def phase_process(command,log,stop_file,root,publish,label):
    """No orphan launch: after STOP finish admitted listing tasks, then return."""
    before=monotonic();peak=0
    with log.open('w',encoding='utf-8') as stream:
        process=subprocess.Popen([sys.executable,'-B',*command],cwd=REPO,stdout=stream,stderr=subprocess.STDOUT,
                                 creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        observed=psutil.Process(process.pid)
        try:
            while process.poll() is None:
                if stop_requested(root) and stop_file is not None:
                    stop_file.parent.mkdir(parents=True,exist_ok=True);stop_file.touch()
                try:peak=max(peak,observed.memory_info().rss)
                except psutil.NoSuchProcess:pass
                progress_path=stop_file.parent/'progress.json' if stop_file else None
                status=label
                if progress_path and progress_path.exists():
                    try:
                        p=read(progress_path);status=label+' '+count_label(p.get('counts',{}))
                    except (OSError,ValueError):pass
                publish(status)
                try:process.wait(timeout=1)
                except subprocess.TimeoutExpired:pass
        finally:
            if process.poll() is None:
                if stop_file is not None:stop_file.touch()
                process.wait()
    return dict(exit_code=process.returncode,elapsed_seconds=monotonic()-before,peak_rss_bytes=peak,log=str(log))


def execute_day(day,root,plan,args,slots,publish):
    folder=root/'days'/str(day);folder.mkdir(parents=True,exist_ok=True)
    scope='all' if not args.tickers else 'canary-'+digest(sorted(set(args.tickers)))[:12]
    p1=runtime_root()/'hindsight-phase1'/str(day)/scope/('campaign-'+plan['extraction_hash'][:16])
    # A STOP marker is never removed automatically, including one from a prior run.
    p1command=['scripts/build_hindsight_phase1.py','--date',str(day),'--run-name',p1.name,
               '--workers',str(slots),'--query-threads','2','--macd-readers',str(args.macd_readers),
               '--macd-window-hours',str(args.macd_window_hours)]
    if args.tickers:p1command+=['--tickers',*args.tickers]
    p1result=phase_process(p1command,folder/'phase1.log',p1/'STOP',root,publish,'Phase 1')
    result=dict(date=str(day),phase1_root=str(p1),phase1=p1result,status='failed')
    if p1result['exit_code']==0 and not stop_requested(root):
        write(folder/'phase1.json',read(p1/'summary.json'),immutable=False)
        handoff=folder/'phase2-location.json'
        # Start the child, wait for its output-root announcement, then use that STOP boundary.
        p2command=['scripts/build_hindsight_greedy.py','build','--phase1',str(p1),'--workers',str(slots),
                   '--gamma',str(args.gamma),'--cost-per-share',str(args.cost_per_share),'--result-file',str(handoff)]
        p2=phase2_process(p2command,folder,root,handoff,publish)
        result['phase2']=p2
        if handoff.exists():
            p2root=Path(read(handoff)['root']);result['phase2_root']=str(p2root)
            if (p2root/'summary.json').exists():write(folder/'phase2.json',read(p2root/'summary.json'),immutable=False)
        if p2['exit_code']==0:result['status']='completed'
    if stop_requested(root):result['status']='interrupted'
    write(folder/'result.json',result,immutable=False)
    return result


def phase2_process(command,folder,root,handoff,publish):
    # Resolve STOP dynamically because the Phase 2 output hash includes its source.
    before=monotonic();peak=0
    with (folder/'phase2.log').open('w',encoding='utf-8') as stream:
        process=subprocess.Popen([sys.executable,'-B',*command],cwd=REPO,stdout=stream,stderr=subprocess.STDOUT,
                                 creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        observed=psutil.Process(process.pid)
        try:
            while process.poll() is None:
                if handoff.exists():
                    p2root=Path(read(handoff)['root'])
                    if stop_requested(root):(p2root/'STOP').touch()
                    try:publish('Phase 2 '+count_label(read(p2root/'progress.json').get('counts',{})))
                    except (OSError,ValueError):publish('Phase 2 preparing')
                try:peak=max(peak,observed.memory_info().rss)
                except psutil.NoSuchProcess:pass
                try:process.wait(timeout=1)
                except subprocess.TimeoutExpired:pass
        finally:
            if process.poll() is None:
                if handoff.exists():(Path(read(handoff)['root'])/'STOP').touch()
                process.wait()
    return dict(exit_code=process.returncode,elapsed_seconds=monotonic()-before,peak_rss_bytes=peak,log=str(folder/'phase2.log'))


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command',choices=('run','benchmark','preflight'))
    parser.add_argument('--start',type=date.fromisoformat)
    parser.add_argument('--end',type=date.fromisoformat)
    parser.add_argument('--date',type=date.fromisoformat)
    parser.add_argument('--workers',type=int,default=None,help='Total listing workers across active dates; auto up to 4')
    parser.add_argument('--day-workers',type=int,choices=range(1,5),default=1)
    parser.add_argument('--macd-readers',type=int,choices=range(1,5),default=2)
    parser.add_argument('--macd-window-hours',type=int,choices=(1,2,4,8),default=1)
    parser.add_argument('--tickers',nargs='+')
    parser.add_argument('--gamma',type=float,default=.99)
    parser.add_argument('--cost-per-share',type=float,default=0)
    parser.add_argument('--qmd-url',help='Existing authoritative QMD History endpoint; inherited by children')
    args=parser.parse_args(argv);console=Console();STOP.clear()
    import math
    if not math.isfinite(args.gamma) or not 0<args.gamma<=1 or not math.isfinite(args.cost_per_share) or args.cost_per_share<0:
        parser.error('gamma must be in (0,1]; cost must be finite and nonnegative')
    runtime,endpoint,fingerprint,workers=preflight(args)
    if args.command=='preflight':
        console.print(f'Ready | workers {workers} | QMD {endpoint} | runtime {runtime}');return 0
    if args.date:
        if args.start or args.end:parser.error('Use --date or --start/--end, not both')
        start=end=args.date
    else:
        if not args.start or not args.end:parser.error('Supply --date or both --start and --end')
        start,end=args.start,args.end
    if start>end:parser.error('start must not be after end')
    days=market_sessions(start,end)
    if not days:parser.error('No XNYS sessions in range')
    if datetime.combine(days[-1],time(20),NY)>datetime.now(timezone.utc):parser.error('Only completed 04:00-20:00 sessions')
    # Preflight every requested trading day; missing published dates fail, never disappear.
    if args.command=='benchmark' and not args.tickers:
        args.tickers=['AAPL','SUGP']
    c=client(1);population_hashes={}
    try:
        for day in days:
            rows=validate_universe(query(c,universe_sql(day)))
            if args.tickers and set(args.tickers)-{r['ticker'] for r in rows}:raise ValueError('Canary ticker not tradable on '+str(day))
            population_hashes[str(day)]=digest(rows)
    finally:c.close()
    source_hashes={p:sha256((REPO/p).read_text(encoding='utf-8').replace('\r\n','\n').encode()).hexdigest() for p in SOURCES}
    extraction=dict(code={k:v for k,v in source_hashes.items() if 'greedy' not in k and 'dataset' not in k},
                    qmd_source_fingerprint=fingerprint,macd_readers=args.macd_readers,macd_window_hours=args.macd_window_hours)
    plan=dict(version='hindsight-campaign-v1',dates=[str(d) for d in days],
        population_hashes=population_hashes,
        tickers=sorted(set(args.tickers)) if args.tickers else None,scope='explicit_canary' if args.tickers else 'full_tradable_universe',
        gamma=args.gamma,cost_per_share=args.cost_per_share,code_hashes=source_hashes,
        extraction_hash=digest(extraction),qmd_source_fingerprint=fingerprint)
    plan['plan_hash']=digest(plan)
    root=runtime/'hindsight-campaigns'/plan['plan_hash'][:20];root.mkdir(parents=True,exist_ok=True)
    slots=max(1,workers//args.day_workers)
    console.print(f'{len(days)} sessions | {plan["scope"]} | {args.day_workers} active dates x {slots} listing workers')
    console.print('Artifacts: '+str(root),soft_wrap=True)
    console.print('Ctrl+C / STOP drains active listings. Resume preserves verified files. No automatic retries.')
    old=signal.signal(signal.SIGINT,lambda *_:STOP.set())
    counts=dict(completed=0,failed=0,interrupted=0);results=[];active={};lock=threading.Lock();started=monotonic()
    try:
        with exclusive(root/'run.lock'):
            write(root/'plan.json',plan);(root/'complete.json').unlink(missing_ok=True)
            def work(day):
                def publish(value):
                    with lock:active[str(day)]=value
                return execute_day(day,root,plan,args,slots,publish)
            pending=iter(days)
            with ThreadPoolExecutor(max_workers=args.day_workers) as pool,Live(console=console,auto_refresh=False) as live:
                futures={}
                def submit():
                    if stop_requested(root):return
                    day=next(pending,None)
                    if day:
                        with lock:active[str(day)]='starting'
                        futures[pool.submit(work,day)]=day
                for _ in range(args.day_workers):submit()
                last=0
                while futures:
                    done,_=wait(futures,timeout=1,return_when=FIRST_COMPLETED)
                    for future in done:
                        day=futures.pop(future)
                        try:result=future.result()
                        except Exception as exc:result=dict(date=str(day),status='failed',error=str(exc))
                        results.append(result);counts[result['status']]+=1
                        with lock:active.pop(str(day),None)
                        submit()
                    with lock:snapshot=dict(active)
                    view=table(counts,snapshot,len(days),monotonic()-started)
                    if console.is_terminal:live.update(view,refresh=True)
                    if monotonic()-last>=5:
                        write(root/'progress.json',dict(counts=counts,active=snapshot,queued=len(days)-len(results)-len(snapshot),elapsed_seconds=monotonic()-started),immutable=False)
                        if not console.is_terminal:console.print(view)
                        last=monotonic()
            success=counts['completed']==len(days)
            summary=dict(status='complete' if success else 'interrupted' if stop_requested(root) else 'failed',
                         counts=counts,results=results,elapsed_seconds=monotonic()-started,workers=workers,day_workers=args.day_workers)
            write(root/'summary.json',summary,immutable=False)
            write(root/'progress.json',summary,immutable=False)
            if success:write(root/'complete.json',dict(plan_hash=plan['plan_hash'],days=len(days)))
            console.print('Result: '+summary['status']+' | '+str(root/'summary.json'),soft_wrap=True)
            for result in results:
                if result['status']=='failed':console.print('Failed '+result['date']+'; inspect days/'+result['date']+'/phase*.log')
            return 0 if success else 2
    finally:signal.signal(signal.SIGINT,old)


if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception as exc:
        print('Dataset campaign failed: '+str(exc),file=sys.stderr);raise SystemExit(2)
