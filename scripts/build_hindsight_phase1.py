"""Build persisted Phase 1 MACD opportunities for one dated tradable universe.

Example: python scripts/build_hindsight_phase1.py --date 2026-08-21
Use the same command to resume. --tickers creates an explicitly scoped canary.
No portfolio selection, sizing, costs, spread cap or activity gate is applied.
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import sys
sys.dont_write_bytecode=True
from pathlib import Path
REPO=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(REPO))
import argparse
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from contextlib import contextmanager
from datetime import date, datetime, time, timezone
from hashlib import sha256
import json, signal, threading
from time import monotonic
from urllib.request import urlopen
from rich.console import Console
from rich.live import Live
from rich.table import Table
from research.mlops.env import load_env_files
from research.mlops.clickhouse import discover_clickhouse_env_files
from src.runtime_paths import runtime_root
from src.market_engine.level_book_store import read, write
from src.market_engine.hindsight_phase1 import VERSION, NY, digest, opportunities, targets_from_extrema
from src.market_engine.hindsight_phase1_source import client, query, metadata, RULE_SQL, universe_sql, validate_universe, extrema_sql, quotes_sql
from src.backend.hindsight_service import load_macd_intervals
from src.backend.qmd_gateway_client import qmd_history_base_url

TRACKED=('scripts/build_hindsight_phase1.py','src/market_engine/hindsight_phase1.py','src/market_engine/hindsight_phase1_source.py','src/market_engine/hindsight.py','src/backend/hindsight_service.py')
STOP=threading.Event()


def file_hash(path):
    with path.open('rb') as f:return sha256(f.read()).hexdigest()


@contextmanager
def exclusive(path):
    with path.open('a+b') as f:
        f.seek(0);f.write(b'0');f.flush();f.seek(0)
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
        yield


def verified(folder,ready):
    for name,expected in ready['files'].items():
        if not (folder/name).is_file() or file_hash(folder/name)!=expected:raise ValueError('Checkpoint integrity failure: '+str(folder/name))


def cached_json(path,producer):
    receipt=path.with_name(path.name+'.sha256.json')
    if receipt.exists():
        if not path.exists() or file_hash(path)!=read(receipt)['sha256']:raise ValueError('Input checkpoint integrity failure: '+str(path))
        return read(path)
    value=producer()
    write(path,value,immutable=False)
    write(receipt,dict(sha256=file_hash(path)))
    return value


def run_listing(row,day,root,plan,lookback,threads,publish):
    ticker=row['ticker'];folder=root/'listings'/digest(row)[:20];folder.mkdir(parents=True,exist_ok=True)
    c=client(threads)
    try:
        publish(ticker,'coverage')
        before=metadata(c,day,ticker)
        if (folder/'source.json').exists() and read(folder/'source.json')!=before:raise ValueError('Source changed; create a new --run-name')
        write(folder/'source.json',before)
        if (folder/'ready.json').exists():
            ready=read(folder/'ready.json');verified(folder,ready)
            if ready['plan_hash']!=plan['plan_hash']:raise ValueError('Checkpoint plan mismatch')
            return dict(ticker=ticker,status='reused',rows=ready['rows'],directory=folder.name)
        publish(ticker,'MACD aggregate coverage')
        macd=folder/'macd.json.gz'
        def prepare_macd():
            start=datetime.combine(day,time(4),NY);end=datetime.combine(day,time(20),NY)
            intervals,provenance=load_macd_intervals(ticker,start,end,lambda **kw:publish(ticker,'MACD '+kw.get('through','')),monotonic()+600)
            return dict(intervals=intervals,provenance=provenance)
        cached=cached_json(macd,prepare_macd)
        publish(ticker,'ClickHouse trade extrema')
        extrema=folder/'extrema.json.gz'
        records=cached_json(extrema,lambda:query(c,extrema_sql(day,ticker,before,plan['rules'])))
        targets=targets_from_extrema(records,cached['intervals'],lookback)
        write(folder/'targets.json',targets)
        publish(ticker,'ClickHouse quote samples')
        quotes=folder/'quotes.json.gz'
        samples=cached_json(quotes,lambda:query(c,quotes_sql(day,ticker,before,targets['positions'])))
        publish(ticker,'vectorized values / Parquet')
        values=opportunities(day,records,samples,targets['positions'])
        import polars as pl
        values=values.with_columns(pl.lit(ticker).alias('ticker'),pl.lit(row['listing_id']).alias('listing_id'))
        temporary=folder/'opportunities.parquet.tmp';temporary.unlink(missing_ok=True)
        values.write_parquet(temporary,compression='zstd',statistics=True)
        if metadata(c,day,ticker)!=before or query(c,RULE_SQL)!=plan['rules']:raise ValueError('Source or condition rules changed during extraction; no completion published')
        temporary.replace(folder/'opportunities.parquet')
        files={name:file_hash(folder/name) for name in ('source.json','macd.json.gz','extrema.json.gz','quotes.json.gz','targets.json','opportunities.parquet')}
        ready=dict(plan_hash=plan['plan_hash'],ticker=ticker,listing=row,rows=values.height,files=files,
                   target_count=len(targets['positions']),completed_at=datetime.now(timezone.utc).isoformat())
        write(folder/'ready.json',ready)
        return dict(ticker=ticker,status='completed',rows=values.height,directory=folder.name)
    finally:c.close()


def progress_table(counts,active,total,elapsed):
    t=Table(title='Phase 1 | tradable listings',expand=True)
    t.add_column('Completed');t.add_column('Reused');t.add_column('Active');t.add_column('Queued');t.add_column('Failed')
    done=counts['completed']+counts['reused']+counts['failed']
    t.add_row(str(counts['completed']),str(counts['reused']),str(len(active)),str(total-done-len(active)),str(counts['failed']))
    t.caption=f'{elapsed:.0f}s elapsed | retries: 0 (rerun retries failed listings) | '+', '.join(f'{k}: {v}' for k,v in sorted(active.items()))
    return t


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--date',required=True,type=date.fromisoformat,help='Completed trading date (New York)')
    parser.add_argument('--tickers',nargs='+',help='Explicit canary subset of the dated tradable universe')
    parser.add_argument('--workers',type=int,choices=range(1,5),default=2)
    parser.add_argument('--query-threads',type=int,choices=range(1,5),default=2)
    parser.add_argument('--lookback-seconds',type=int,choices=range(0,31),default=2,help='Base hindsight whole-second swing lookback')
    parser.add_argument('--run-name',default='default',help='New name required after changing pinned source/configuration')
    parser.add_argument('--plan-only',action='store_true',help='Freeze listing plan without calculating opportunities')
    args=parser.parse_args(argv);console=Console();STOP.clear()
    if not args.run_name.replace('-','').replace('_','').isalnum():parser.error('run-name must contain letters, numbers, hyphens or underscores')
    if datetime.combine(args.date,time(20),NY)>datetime.now(timezone.utc):parser.error('Select a completed 04:00-20:00 New York session')
    runtime=runtime_root()
    if not runtime.is_dir():raise ValueError('Required runtime root unavailable: '+str(runtime))
    load_env_files(discover_clickhouse_env_files(),verbose=False)
    c=client(args.query_threads)
    try:
        universe=validate_universe(query(c,universe_sql(args.date)))
        rules=query(c,RULE_SQL)
        if not rules:raise ValueError('Canonical condition rules missing')
    finally:c.close()
    names={r['ticker'] for r in universe};selected=sorted(set(args.tickers or names))
    if set(selected)-names:raise ValueError('Tickers not in dated tradable universe: '+', '.join(sorted(set(selected)-names)))
    selected_set=set(selected)
    selected_rows=[r for r in universe if r['ticker'] in selected_set]
    with urlopen(qmd_history_base_url().rstrip('/')+'/health',timeout=20) as response:health=json.load(response)
    runtime_hash=health.get('source_fingerprint')
    if not runtime_hash:raise ValueError('QMD History omitted its source fingerprint')
    plan=dict(version=VERSION,date=str(args.date),session='04:00-20:00 America/New_York',universe=universe,
              selected=selected_rows,scope='full_tradable_universe' if args.tickers is None else 'explicit_canary',
              lookback_seconds=args.lookback_seconds,rules=rules,qmd_source_fingerprint=runtime_hash,
              code_hashes={p:sha256((REPO/p).read_text(encoding='utf-8').replace('\r\n','\n').encode()).hexdigest() for p in TRACKED},
              polars_version=__import__('polars').__version__,
              filters='Dated is_tradable=1 only. No spread, volume, price, cost or capital selection.',
              semantics='Independent one-share raw action values. Not final market policy labels.')
    plan['plan_hash']=digest(plan)
    scope='all' if args.tickers is None else 'canary-'+digest(selected)[:12]
    root=runtime/'hindsight-phase1'/str(args.date)/scope/args.run_name;root.mkdir(parents=True,exist_ok=True)
    console.print(f'{args.date} | {len(selected_rows):,}/{len(universe):,} tradable listings | {args.workers} workers')
    console.print('Artifacts: '+str(root),soft_wrap=True)
    with exclusive(root/'run.lock'):
        write(root/'plan.json',plan)
        if args.plan_only:
            console.print('Plan saved. No opportunities calculated.');return 0
        (root/'complete.json').unlink(missing_ok=True)
        active={};lock=threading.Lock();counts=dict(completed=0,reused=0,failed=0);results=[];started=monotonic()
        def publish(ticker,stage):
            with lock:active[ticker]=stage
        old_handler=signal.signal(signal.SIGINT,lambda *_:STOP.set())
        pending=iter(selected_rows)
        try:
            with ThreadPoolExecutor(max_workers=args.workers) as pool, Live(console=console,auto_refresh=False) as live:
                futures={}
                def submit():
                    if STOP.is_set() or (root/'STOP').exists():return
                    row=next(pending,None)
                    if row is not None:
                        publish(row['ticker'],'starting');futures[pool.submit(run_listing,row,args.date,root,plan,args.lookback_seconds,args.query_threads,publish)]=row
                for _ in range(args.workers):submit()
                last_log=0
                while futures:
                    finished,_=wait(futures,timeout=1,return_when=FIRST_COMPLETED)
                    for future in finished:
                        row=futures.pop(future)
                        try:result=future.result()
                        except Exception as exc:result=dict(ticker=row['ticker'],status='failed',error=str(exc))
                        results.append(result);counts[result['status']]+=1
                        with lock:active.pop(row['ticker'],None)
                        with lock:snapshot=dict(active)
                        write(root/'progress.json',dict(counts=counts,results=results,active=snapshot),immutable=False)
                        submit()
                    with lock:table=progress_table(counts,dict(active),len(selected_rows),monotonic()-started)
                    if STOP.is_set() or (root/'STOP').exists():table.caption='Stop requested: finishing active listings; no new listings will start.'
                    if console.is_terminal:live.update(table,refresh=True)
                    if monotonic()-last_log>=10:
                        with lock:snapshot=dict(active)
                        write(root/'progress.json',dict(counts=counts,results=results,active=snapshot,updated_at=datetime.now(timezone.utc).isoformat()),immutable=False)
                        if not console.is_terminal:console.print(table)
                        last_log=monotonic()
            complete=len(results)==len(selected_rows) and counts['failed']==0
            summary=dict(status='complete' if complete else 'interrupted' if len(results)<len(selected_rows) else 'failed',
                         plan_hash=plan['plan_hash'],counts=counts,results=results,elapsed_seconds=monotonic()-started)
            write(root/'summary.json',summary,immutable=False)
            if complete:write(root/'complete.json',dict(plan_hash=plan['plan_hash'],listing_count=len(results),rows=sum(r['rows'] for r in results)))
            console.print(progress_table(counts,{},len(selected_rows),monotonic()-started));console.print('Result: '+summary['status'])
            for r in results:
                if r['status']=='failed':console.print(r['ticker']+': '+r['error'][:240]+' (full error: summary.json)')
            return 0 if complete else 2
        finally:signal.signal(signal.SIGINT,old_handler)


if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception as exc:
        print('Phase 1 failed: '+str(exc),file=sys.stderr);raise SystemExit(2)
