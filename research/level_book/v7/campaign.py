"""Frozen-universe, restart-safe V7 history; laptop workers, workstation SQL."""
import argparse
from collections import Counter
from contextlib import contextmanager
from datetime import date,datetime,timezone
from hashlib import sha256
import atexit,json,os,signal,subprocess,sys,time,traceback,shutil
from pathlib import Path
REPO=Path(__file__).resolve().parents[3]
# Existing split-audit helpers use script-local imports, as their launchers do.
if str(REPO/'scripts') not in sys.path:sys.path.insert(0,str(REPO/'scripts'))
import numpy as np
import scipy
from rich.console import Console
from rich.table import Table
from rich.live import Live
from research.mlops.env import load_env_files
from research.mlops.clickhouse import discover_clickhouse_env_files,ClickHouseHttpClient,default_clickhouse_url,default_clickhouse_user,default_clickhouse_password
from src.market_engine.level_book_store import ROOT
from .campaign_store import read,write,verified_book
from src.market_engine.historical_level_checkpoint import digest
from src.market_engine.historical_session_levels import Settings
from src.market_engine.streaming_level_book import StreamingLevelBook,EXTRACTION_VERSION
from src.market_engine.derived_trade_policy import POLICY
from src.market_engine.reaction_band import CONFIG
from src.backend.swing_book_source import session_bounds,HISTORICAL_POLICY
from scripts.swing_book_paths import ticker_directory
from src.runtime_paths import WORKSTATION_RUNTIME_ROOT
from scripts.build_structure_book_clickhouse import canonical_splits
from .campaign_source import (literal,coverage_sql,bars_sql,decode,source_hash,
    reporting_coverage_sql,require_reporting_coverage,REPORTING_REVISION)

VERSION='all-tradable-v7-mle-campaign-1'
TRACKED=('research/level_book/v7/campaign.py','research/level_book/v7/campaign_source.py','research/level_book/v7/campaign_store.py',
 'scripts/build_level_book_v7_campaign.py','src/market_engine/streaming_level_book.py',
 'src/market_engine/derived_trade_policy.py',
 'src/market_engine/reaction_band.py','src/market_engine/reaction_center.py',
 'src/market_engine/historical_session_levels.py','src/market_engine/historical_level_checkpoint.py',
 'src/market_engine/level_book_store.py','src/backend/swing_book_source.py','src/backend/swing_book_indexed_source.py',
 'scripts/build_structure_book_clickhouse.py','scripts/swing_book_paths.py',
 'research/level_book/v7/direct_publisher.py','scripts/build_level_book_v7_direct.py')
RULE_SQL="SELECT token_id,modifier_int,update_high_low,update_last,update_volume FROM market_sip_compact.event_condition_token_reference WHERE source_family='trade_conditions' AND is_join_canonical=1 ORDER BY token_id"
CLIENTS={}
atexit.register(lambda:[c.close() for c in CLIENTS.values()])

def now():return datetime.now(timezone.utc).isoformat()
def hashes():return {p:sha256((REPO/p).read_bytes()).hexdigest() for p in TRACKED}

def query(sql,threads=2):
    client=CLIENTS.get(threads)
    if client is None:
        client=ClickHouseHttpClient(default_clickhouse_url(),default_clickhouse_user(),default_clickhouse_password(),timeout_seconds=180,persistent=True,
            default_query_params=dict(readonly=1,max_threads=threads,max_memory_usage=1073741824,max_result_rows=100000,max_result_bytes=64000000,result_overflow_mode='throw'))
        CLIENTS[threads]=client
    return [json.loads(s) for s in client.execute(sql+' FORMAT JSONEachRow').splitlines() if s]

@contextmanager
def exclusive(path):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a+b') as f:
        f.seek(0);f.write(b'0');f.flush();f.seek(0)
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
        yield

def paths(root,ticker):return root/'tickers'/ticker_directory(ticker)

def plan(args):
    root=args.runtime
    if (root/'plan.json').exists():raise ValueError('Plan exists; run resumes it without replanning')
    if args.end>date.today().isoformat() or args.start>args.end:raise ValueError('Invalid or future historical interval')
    universe_day=query(f"SELECT toString(max(universe_date)) day FROM q_live.feature_tradable_universe_v1 FINAL WHERE universe_date<={literal(args.end)}")[0]['day']
    universe=query(f"SELECT ticker,symbol_id,listing_id,security_id,ibkr_conid,massive_ticker,source_run_id FROM q_live.feature_tradable_universe_v1 FINAL WHERE universe_date={literal(universe_day)} AND is_tradable=1 ORDER BY ticker,symbol_id")
    if not universe:raise ValueError('Published tradable universe is empty')
    grouped={}
    for u in universe:grouped.setdefault(u['ticker'],[]).append(u)
    if args.tickers:
        missing=set(args.tickers)-grouped.keys()
        if missing:raise ValueError('Requested tickers absent from published tradable universe: '+', '.join(sorted(missing)))
        grouped={k:v for k,v in grouped.items() if k in args.tickers}
    coverage={};names=sorted(grouped)
    # Bound aggregate arrays while hashing source-day metadata. A universe-wide
    # groupArray/sort can exceed the query budget before any worker starts.
    for offset in range(0,len(names),128):
        coverage.update({r['ticker']:r for r in query(coverage_sql(args.start,args.end,names[offset:offset+128]))})
        if offset%512==0 or offset+128>=len(names):print(f'Planning certified coverage: {min(offset+128,len(names)):,}/{len(names):,} symbols',flush=True)
    source_days = [r['source_date'] for r in query(
        f"SELECT DISTINCT toString(source_date) source_date FROM market_sip_compact.events_ordinal_continuity FINAL "
        f"WHERE source_date BETWEEN {literal(args.start)} AND {literal(args.end)} ORDER BY source_date")]
    reporting_rows=query(reporting_coverage_sql(args.start,args.end))
    require_reporting_coverage(source_days, reporting_rows)
    rows=[]
    for ticker,identities in grouped.items():
        reason='';c=coverage.get(ticker)
        try:directory=ticker_directory(ticker)
        except ValueError:directory=None;reason='unsupported canonical ticker syntax'
        if len(identities)!=1:reason='ambiguous published ticker identity'
        elif identities[0].get('massive_ticker') not in ('',None,ticker):reason='market ticker mapping requires identity review'
        elif not c:reason='no certified canonical history in requested range'
        rows.append(dict(ticker=ticker,identity=identities,directory=directory,coverage=c,status='deferred' if reason else 'queued',reason=reason))
    rows.sort(key=lambda r:(-(r['coverage'] or {}).get('events',0),r['ticker']))
    server=query('SELECT hostName() host')[0]['host'];rules=query(RULE_SQL)
    if not rules:raise ValueError('Trade condition rules missing')
    value=dict(version=VERSION,created_at=now(),start=args.start,end=args.end,universe_date=universe_day,
        population_contract='published tradable membership as of universe_date; not historical membership eligibility',
        source_policy=HISTORICAL_POLICY,input_policy=POLICY,reporting_revision=REPORTING_REVISION,
        reporting_coverage_hash=digest(reporting_rows),
        output_contract=getattr(args,'output_contract','filesystem-v1'),
        server=server,source_files=hashes(),git_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=REPO,text=True).strip(),
        band_config=CONFIG,extraction_version=EXTRACTION_VERSION,software=dict(python=sys.version,numpy=np.__version__,scipy=scipy.__version__),rules=rules,rows=rows)
    value['plan_hash']=digest(value);write(root/'plan.json',value)
    print(f"Frozen {len(rows):,} symbols as of {universe_day}; {sum(r['status']=='deferred' for r in rows):,} deferred. Server: {server}",flush=True)
    return value

def checked_plan(root):
    p=read(root/'plan.json')
    if p.get('version')!=VERSION or p.get('plan_hash')!=digest({k:v for k,v in p.items() if k!='plan_hash'}):raise ValueError('Plan identity/hash mismatch')
    if p['source_files']!=hashes():raise ValueError('Pinned source code changed; do not mix algorithms in this campaign')
    if p.get('reporting_revision')!=REPORTING_REVISION:raise ValueError('Frozen V7 campaign lacks certified delayed-trade exclusion; rebuild under a new campaign')
    if p['software']!=dict(python=sys.version,numpy=np.__version__,scipy=scipy.__version__):raise ValueError('Pinned numerical runtime changed')
    return p

def fit_day(prior,ticker,day,bars,actions):
    start,end=(t.timestamp() for t in session_bounds(day))
    if prior is None:
        prior=dict(ticker=ticker,session='0001-01-01',available_at=start,levels=[],source_extraction_version=EXTRACTION_VERSION,band_config=CONFIG)
        prior['checkpoint_hash']=digest(prior)
    factor=float(np.prod([float(s['split_from'])/float(s['split_to']) for s in actions]))
    ranges=np.array([b['high']-b['low'] for b in bars]);lo=min(b['low'] for b in bars);hi=max(b['high'] for b in bars)
    tick=.0001 if lo<1 else .01;noise=float(np.median(ranges[ranges>0])) if np.any(ranges>0) else tick
    engine=StreamingLevelBook(prior,ticker=ticker,session=day,start=start,end=end,split_factor=factor,split_evidence=actions,
        settings=Settings(tick=tick),discovery_prominence=max(3*tick,6*noise,(hi-lo)*.05))
    return engine

def worker(args):
    root=args.runtime;p=checked_plan(root);row=next(r for r in p['rows'] if r['ticker']==args.ticker)
    if p.get('output_contract','filesystem-v1')!='filesystem-v1':
        raise ValueError('Direct V7 plan cannot write checkpoint books to disk')
    if row['status']=='deferred':raise ValueError(row['reason'])
    target=paths(root,args.ticker);target.mkdir(parents=True,exist_ok=True);started=time.monotonic()
    progress=dict(ticker=args.ticker,state='active',stage='preflight',completed=0,total=row['coverage']['days'],resumed=0,empty=0,retried=0,pid=os.getpid())
    def publish(**kw):
        progress.update(kw,updated_at=now(),elapsed_seconds=time.monotonic()-started);write(target/'progress.json',progress,immutable=False)
    def q(sql):
        for attempt in range(3):
            try:return query(sql,args.threads)
            except Exception as exc:
                transient=any(s in str(exc).lower() for s in ('timed out','connection','10054','10060','temporarily','http 503','http 502'))
                if not transient or attempt==2:raise
                publish(retried=progress['retried']+1,stage='retrying query');time.sleep(2**attempt)
    with exclusive(target/'worker.lock'):
      try:
        publish()
        if q(coverage_sql(p['start'],p['end'],[args.ticker]))!=[row['coverage']]:raise ValueError('Certified history differs from frozen coverage')
        if q(RULE_SQL)!=p['rules']:raise ValueError('Trade condition rules changed')
        predicate=f"ticker={literal(args.ticker)} AND source_date BETWEEN {literal(p['start'])} AND {literal(p['end'])}"
        days=q('SELECT * FROM market_sip_compact.events_ordinal_continuity FINAL WHERE '+predicate+' ORDER BY source_date')
        reporting_rows=q(reporting_coverage_sql(p['start'],p['end']))
        require_reporting_coverage([d['source_date'] for d in days], reporting_rows)
        reporting_hash=digest(reporting_rows)
        if p.get('reporting_coverage_hash') is not None and reporting_hash!=p['reporting_coverage_hash']:
            raise ValueError('Canonical trade-reporting coverage differs from frozen plan')
        if p.get('reporting_coverage_hash') is None and p.get('reporting_coverage_contract')!='source-plan-v1':
            raise ValueError('Frozen plan lacks trade-reporting coverage authority')
        splits=canonical_splits(q(f"SELECT execution_date,split_from,split_to,inserted_at FROM q_live.market_stock_split_v1 FINAL WHERE provider_ticker={literal(args.ticker)} AND execution_date BETWEEN {literal(p['start'])} AND {literal(p['end'])} ORDER BY execution_date"))
        source=dict(days=days,splits=splits,plan_hash=p['plan_hash'],
                    reporting_coverage_hash=reporting_hash)
        write(target/'source-plan.json',source)
        prior=None
        for metadata in days:
            day=metadata['source_date']
            if (root/'STOP').exists():publish(state='interrupted',stage='stopped at checkpoint');return
            if shutil.disk_usage(root).free<10*1024**3:raise ValueError('Runtime disk has less than 10 GiB free; refusing new checkpoints')
            receipt_path=target/'receipts'/f'{day}.json';book_path=target/'books'/f'{day}.json.gz'
            expected=source_hash(metadata,p['rules']);parent=prior['checkpoint_hash'] if prior else None
            if receipt_path.exists():
                receipt=read(receipt_path)
                if receipt['source_hash']!=expected or receipt['parent_hash']!=parent:raise ValueError('Resume source/parent hash mismatch')
                if receipt['state'] not in ('empty','complete'):raise ValueError('Invalid receipt state')
                if receipt['state']=='complete':
                    prior=verified_book(book_path)
                    if prior['checkpoint_hash']!=receipt['checkpoint_hash']:raise ValueError('Resume checkpoint/receipt mismatch')
                else:progress['empty']+=1
                progress['resumed']+=1
            else:
                publish(stage='ClickHouse OHLCV',session=day,bars_processed=0)
                begin=time.monotonic();bars,audit=decode(q(bars_sql(args.ticker,day,p['rules'],metadata)))
                current=q(f"SELECT * FROM market_sip_compact.events_ordinal_continuity FINAL WHERE ticker={literal(args.ticker)} AND source_date={literal(day)}")
                if current!=[metadata]:raise ValueError('Canonical source changed during aggregation')
                sql_seconds=time.monotonic()-begin;bar_hash=digest(bars)
                receipt=dict(source_hash=expected,parent_hash=parent,bar_hash=bar_hash,bars=len(bars),audit=audit,sql_seconds=sql_seconds)
                if not bars:
                    receipt.update(state='empty',reason='no eligible price seconds');progress['empty']+=1
                else:
                    actions=[s for s in splits if prior and prior['session']<s['execution_date']<=day]
                    publish(stage='MLE fitting',bars_total=len(bars));begin=time.monotonic();engine=fit_day(prior,args.ticker,day,bars,actions);last=begin
                    for i,b in enumerate(bars):
                        engine.update(b,observed_at=b['t'])
                        if i%1000==0 and time.monotonic()-last>=1:publish(bars_processed=i);last=time.monotonic()
                    prior=engine.historical_checkpoint(bar_hash);write(book_path,prior)
                    receipt.update(state='complete',checkpoint_hash=prior['checkpoint_hash'],fit_seconds=time.monotonic()-begin,
                        levels=sum(r['qualified'] for r in prior['levels']),candidates=sum(not r['qualified'] for r in prior['levels']))
                write(receipt_path,receipt)
            publish(completed=progress['completed']+1,session=day,stage='checkpoint saved')
        if q(coverage_sql(p['start'],p['end'],[args.ticker]))!=[row['coverage']]:raise ValueError('Source changed before publication')
        if q(RULE_SQL)!=p['rules']:raise ValueError('Rules changed before publication')
        if digest(q(reporting_coverage_sql(p['start'],p['end'])))!=reporting_hash:
            raise ValueError('Trade-reporting coverage changed before publication')
        write(target/'ready.json',dict(plan_hash=p['plan_hash'],ticker=args.ticker,first_session=days[0]['source_date'],last_session=days[-1]['source_date'],
            sessions=len(days),empty=progress['empty'],book_session=prior['session'] if prior else None,checkpoint_hash=prior['checkpoint_hash'] if prior else None,
            source_plan_hash=digest(source),available_after_session_close=True))
        publish(state='complete',stage='verified',levels=sum(r['qualified'] for r in prior['levels']) if prior else 0)
      except BaseException as exc:
        publish(state='failed',stage='failed',reason=str(exc));write(target/'error.json',dict(at=now(),error=str(exc),traceback=traceback.format_exc()),immutable=False);raise

def duration(seconds):
    return '--' if seconds is None else f'{seconds/3600:.1f}h' if seconds>=3600 else f'{seconds/60:.1f}m'

def render(state,progress,width=110):
    counts=Counter(r['state'] for r in state['rows'].values());done=state.get('sessions_completed',sum(r.get('completed',0) for r in progress.values()));total=state['sessions_total']
    elapsed=max(1,time.time()-state['started_epoch']);new=max(0,done-state.get('initial_completed',0));rate=new/elapsed
    eta=(total-done)/rate if new>=20 else None
    table=Table(title=f"V7 MLE history | {state['state']} | {done:,}/{total:,} sessions",expand=True)
    for c in ('Worker','Ticker','Session','Stage','Progress','Age'):table.add_column(c,no_wrap=True)
    for slot in range(state['workers']):
        active=next((t for t,r in state['rows'].items() if r.get('slot')==slot and r['state']=='active'),None);p=progress.get(active,{})
        age=time.time()-datetime.fromisoformat(p['updated_at']).timestamp() if p.get('updated_at') else None
        table.add_row(str(slot+1),active or '--',p.get('session','--'),p.get('stage','idle'),f"{p.get('completed',0)}/{p.get('total','--')}" if active else '--',f'{age:.0f}s' if age is not None else '--')
    freshness=time.time()-datetime.fromisoformat(state['updated_at']).timestamp()
    table.caption=f"Symbols: active {counts['active']} | queued {counts['queued']} | complete {counts['complete']} | deferred {counts['deferred']} | failed {counts['failed']} | interrupted {counts['interrupted']}\nElapsed {duration(elapsed)} | {rate*60:.1f} sessions/min | ETA {duration(eta)} (mixed ticker workload) | retries {state.get('retried',0)} | controller age {freshness:.0f}s\nCtrl+C or stop command: finish active session checkpoints, then stop."
    return table

def load_progress(root,p):
    values={}
    for row in p['rows']:
        if row['directory'] is None:continue
        path=paths(root,row['ticker'])/'progress.json'
        if path.exists():values[row['ticker']]=read(path)
    return values

def run(args):
    root=args.runtime
    if not 1<=args.workers<=8 or not 1<=args.threads<=4 or args.workers*args.threads>16:raise ValueError('Laptop budget: 1..8 workers, 1..4 query threads, combined <=16')
    with exclusive(root/'controller.lock'):
        p=checked_plan(root) if (root/'plan.json').exists() else plan(args)
        # Check abandoned workers before removing the stop request or spawning.
        for row in p['rows']:
            if row['status']!='deferred':
                lock=paths(root,row['ticker'])/'worker.lock'
                if lock.exists():
                    with exclusive(lock):pass
        (root/'STOP').unlink(missing_ok=True)
        progress=load_progress(root,p);rows={}
        for r in p['rows']:
            saved=progress.get(r['ticker'],{});status=r['status']
            if saved.get('state')=='complete':
                ready=read(paths(root,r['ticker'])/'ready.json')
                if ready['plan_hash']!=p['plan_hash']:raise ValueError('Completed ticker belongs to another plan')
                if ready['checkpoint_hash']:
                    checkpoint=verified_book(paths(root,r['ticker'])/'books'/f"{ready['book_session']}.json.gz")
                    if checkpoint['checkpoint_hash']!=ready['checkpoint_hash']:raise ValueError('Completed ticker checkpoint mismatch')
                status='complete'
            elif saved.get('state')=='failed' and not args.retry_failed:status='failed'
            rows[r['ticker']]=dict(state=status,reason=r['reason'])
        state=dict(state='running',pid=os.getpid(),started_epoch=time.time(),updated_at=now(),workers=args.workers,threads=args.threads,
            sessions_total=sum((r['coverage'] or {}).get('days',0) for r in p['rows'] if r['status']!='deferred'),
            initial_completed=sum(v.get('completed',0) for v in progress.values()),rows=rows)
        console=Console();active={};stopping=False;last_plain=0
        old=signal.signal(signal.SIGINT,lambda *_:(root/'STOP').touch())
        try:
          with Live(console=console,auto_refresh=False,transient=False) as display:
            while True:
                if (root/'STOP').exists():stopping=True;state['state']='stopping'
                for slot,(ticker,proc,log) in list(active.items()):
                    if proc.poll() is None:continue
                    log.close();saved=read(paths(root,ticker)/'progress.json') if (paths(root,ticker)/'progress.json').exists() else {}
                    progress[ticker]=saved;rows[ticker].update(state=saved.get('state','failed'),exit_code=proc.returncode,
                        reason=saved.get('reason') or (f'Worker exited {proc.returncode}; see {log.name}' if proc.returncode else ''))
                    if proc.returncode and rows[ticker]['state']=='active':rows[ticker]['state']='failed'
                    del active[slot]
                if not stopping:
                    for slot in range(args.workers):
                        if slot in active:continue
                        ticker=next((r['ticker'] for r in p['rows'] if rows[r['ticker']]['state']=='queued'),None)
                        if ticker is None:break
                        target=paths(root,ticker);target.mkdir(parents=True,exist_ok=True);log=(target/'worker.log').open('a',encoding='utf-8')
                        cmd=[sys.executable,'-B',str(REPO/'scripts/build_level_book_v7_campaign.py'),'worker','--runtime',str(root),'--ticker',ticker,'--threads',str(args.threads)]
                        proc=subprocess.Popen(cmd,cwd=REPO,stdout=log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name=='nt' else 0)
                        active[slot]=(ticker,proc,log);rows[ticker].update(state='active',slot=slot,pid=proc.pid)
                for ticker,_,_ in active.values():
                    path=paths(root,ticker)/'progress.json'
                    if path.exists():progress[ticker]=read(path)
                state.update(updated_at=now(),sessions_completed=sum(x.get('completed',0) for x in progress.values()),retried=sum(x.get('retried',0) for x in progress.values()),
                    worker_progress={t:progress[t] for t,_,_ in active.values() if t in progress})
                write(root/'status.json',state,immutable=False)
                if console.is_terminal:display.update(render(state,progress),refresh=True)
                elif time.time()-last_plain>=15:
                    console.print(f"{state['state']} | sessions {state['sessions_completed']:,}/{state['sessions_total']:,} | {dict(Counter(r['state'] for r in rows.values()))}");last_plain=time.time()
                if not active:break
                time.sleep(2)
            state['state']='interrupted' if stopping else 'complete_with_gaps' if any(r['state'] in ('failed','deferred') for r in rows.values()) else 'complete'
            state['updated_at']=now();write(root/'status.json',state,immutable=False);console.print(render(state,progress))
        finally:
            signal.signal(signal.SIGINT,old)
            if active:
                (root/'STOP').touch()
                console.print('Waiting for workers to save their active session checkpoints…')
                for _,proc,log in active.values():proc.wait();log.close()
    if any(r['state']=='failed' for r in rows.values()):raise SystemExit(1)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['plan','run','worker','status','monitor','stop'])
    parser.add_argument('--runtime',type=Path,default=WORKSTATION_RUNTIME_ROOT/'level-book-v7'/'all-tradable-20250101-20260912-mle-v1')
    parser.add_argument('--start',default='2025-01-01');parser.add_argument('--end',default='2026-09-12')
    parser.add_argument('--workers',type=int,default=4);parser.add_argument('--threads',type=int,default=2)
    parser.add_argument('--ticker');parser.add_argument('--tickers',nargs='+');parser.add_argument('--retry-failed',action='store_true')
    args=parser.parse_args();args.runtime=args.runtime.resolve()
    if not any(base.parent.is_dir() and args.runtime.is_relative_to(base.resolve()) for base in (ROOT,WORKSTATION_RUNTIME_ROOT/'level-book-v7')):raise ValueError('Required V7 runtime root unavailable or invalid')
    for d in (args.start,args.end):date.fromisoformat(d)
    args.runtime.mkdir(parents=True,exist_ok=True)
    if args.command=='stop':(args.runtime/'STOP').touch();print('Stop requested; active session checkpoints will finish.');return
    if args.command in ('status','monitor'):
        console=Console()
        if args.command=='status':
            state=read(args.runtime/'status.json');console.print(render(state,state.get('worker_progress',{})));return
        with Live(console=console,auto_refresh=False) as live:
            while True:
                state=read(args.runtime/'status.json');live.update(render(state,state.get('worker_progress',{})),refresh=True)
                if state['state'] not in ('running','stopping'):break
                time.sleep(3)
        return
    load_env_files(discover_clickhouse_env_files(),verbose=False)
    if args.command=='plan':
        with exclusive(args.runtime/'controller.lock'):plan(args)
    elif args.command=='worker':worker(args)
    else:run(args)

if __name__=='__main__':main()
