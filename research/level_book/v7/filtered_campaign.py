"""One restart-safe workstation campaign for all published filtered V7 histories."""
import argparse
from collections import Counter, deque
from concurrent.futures import ProcessPoolExecutor
from contextlib import redirect_stdout, redirect_stderr
from datetime import date, datetime, timedelta
import gc
from hashlib import sha256
import multiprocessing
import os
from pathlib import Path
import signal
import sys
import time
from types import SimpleNamespace
import uuid

import psutil
from rich.console import Console, Group
from rich.live import Live
from rich.table import Table
from rich.text import Text

from . import campaign as c
from .filtered_prefix import worker
from .workstation import resource_budget, initialize_worker, GIB
from src.market_engine.v7_catalog import Catalog, shared_root
from src.market_engine.filtered_v7_history import kernel, successor
from src.runtime_paths import WORKSTATION_NAME

NAME = 'filtered-v7-workstation-v1'
EXECUTION_FILES = ('research/level_book/v7/filtered_campaign.py',
    'research/level_book/v7/filtered_prefix.py', 'research/level_book/v7/workstation.py',
    'scripts/prepare_filtered_v7.py', 'src/market_engine/filtered_v7_history.py',
    'src/market_engine/v7_catalog.py')


def execution_hashes():
    return {name:sha256((c.REPO/name).read_bytes()).hexdigest() for name in EXECUTION_FILES}


def make_plan(root):
    catalog = Catalog(root)
    reasons_by_ticker = {}
    for _,parent in catalog.plans:
        for row in parent['rows']:
            reasons_by_ticker.setdefault(row['ticker'],set()).add(row.get('reason','unresolved authority'))
    names = sorted(reasons_by_ticker)
    rows = []
    for ticker in names:
        candidates = [(folder,p,r) for folder,p,r in catalog.by_ticker.get(ticker,[]) if p.get('input_policy') != c.POLICY]
        if not candidates:
            reasons = sorted(reasons_by_ticker[ticker])
            rows.append(dict(ticker=ticker,state='deferred',reason='; '.join(reasons)))
            continue
        folder,parent,row = candidates[-1]
        # Narrow the row search without altering the frozen parent identity.
        output,plan = successor(root,dict(parent,rows=[row]),ticker)
        rows.append(dict(ticker=ticker,state='queued',parent=str(folder.relative_to(root)),
            parent_hash=parent['plan_hash'],plan_hash=plan['plan_hash'],
            output=str(output.relative_to(root)),directory=row['directory'],
            before=(date.fromisoformat(parent['end'])+timedelta(days=1)).isoformat(),
            sessions=row['coverage']['days']))
    rows.sort(key=lambda row:(-row.get('sessions',0),row['ticker']))
    plan = dict(version=1,catalog_hash=catalog.fingerprint,consumer_kernel=kernel(),
                scheduler_hashes=execution_hashes(),rows=rows)
    plan['manifest_hash'] = c.digest(plan)
    return plan


def checked_plan(folder):
    plan = c.read(folder/'plan.json')
    if plan.get('manifest_hash') != c.digest({k:v for k,v in plan.items() if k!='manifest_hash'}):
        raise ValueError('Filtered campaign manifest hash mismatch')
    if plan['consumer_kernel'] != kernel():
        raise ValueError('Code/Python/NumPy/SciPy differ from the laptop consumer contract; do not rebuild under a different identity')
    if plan['scheduler_hashes'] != execution_hashes():
        raise ValueError('Scheduler source changed; export a new named campaign from the laptop')
    return plan


def execute(root, folder, row):
    root,folder = Path(root),Path(folder)
    checked_plan(folder)
    parent = c.read(root/row['parent']/'plan.json')
    if parent['plan_hash'] != row['parent_hash'] or c.digest({k:v for k,v in parent.items() if k!='plan_hash'}) != row['parent_hash']:
        raise ValueError('Frozen parent campaign changed')
    output,plan = successor(root,parent,row['ticker'])
    if plan['plan_hash'] != row['plan_hash'] or output != root/row['output']:
        raise ValueError('Filtered successor identity differs from consumer contract')
    while True:
        if (folder/'STOP').exists():
            return dict(state='interrupted',reason='Stopped before ticker lock',completed=0)
        lock = c.exclusive(output/'preparation.lock')
        try:
            lock.__enter__();break
        except OSError as exc:
            if getattr(exc,'errno',None) not in (11,13,36):raise
            time.sleep(1)
    try:
        c.write(output/'plan.json',plan)
        with (output/'workstation-preparation.log').open('a',encoding='utf-8',buffering=1) as log:
            with redirect_stdout(log),redirect_stderr(log):
                worker(SimpleNamespace(runtime=output,ticker=row['ticker'],threads=1,
                    before=row['before'],stop_file=folder/'STOP'))
        result = c.read(c.paths(output,row['ticker'])/'progress.json')
        result['reused'] = result['resumed']==result['total']
        return result
    finally:
        lock.__exit__(None,None,None)
        from src.market_engine.reaction_band import cached_fit
        cached_fit.cache_clear();gc.collect()


def render(state, width=100, height=30, page=1):
    counts = Counter(r['state'] for r in state['rows'].values())
    elapsed = max(1,time.time()-state['started_epoch'])
    new = sum(max(0,p.get('completed',0)-p.get('resumed',0)) for p in state['progress'].values())
    rate = new/elapsed
    remaining = max(0,state['sessions_total']-state['sessions_completed'])
    age = max(0,time.time()-datetime.fromisoformat(state['updated_at']).timestamp())
    summary = Text(f"Filtered V7 | {state['state']} | {state['sessions_completed']:,}/{state['sessions_total']:,} verified sessions\n")
    summary.append(f"Active {counts['active']} | queued {counts['queued']} | complete {counts['complete']} | reused {state.get('reused',0)}\n")
    summary.append(f"Failed {counts['failed']} | deferred {counts['deferred']} | interrupted {counts['interrupted']} | retries {state.get('retried',0)}\n")
    eta = c.duration(remaining/rate) if new>=10 and rate and state['state']=='running' else '--'
    summary.append(f"{rate*60:.1f} new sessions/min | ETA {eta} | controller age {age:.0f}s")
    slots = max(1,height-12);pages=max(1,(state['workers']+slots-1)//slots)
    page=min(max(1,page),pages);start=(page-1)*slots
    active={r['slot']:t for t,r in state['rows'].items() if r['state']=='active'}
    table=Table(expand=True,box=None,padding=(0,1))
    for title in ('ID','Ticker','Stage / session','Sessions','Age'):
        table.add_column(title,no_wrap=True,overflow='ellipsis')
    for slot in range(start,min(start+slots,state['workers'])):
        ticker=active.get(slot);p=state['progress'].get(ticker,{})
        worker_age=f"{max(0,time.time()-datetime.fromisoformat(p['updated_at']).timestamp()):.0f}s" if p.get('updated_at') else '--'
        stage=p.get('stage','starting' if ticker else 'idle')
        table.add_row(str(slot+1),ticker or '--',stage+' / '+p.get('session','--'),
            f"{p.get('completed',0)}/{p.get('total','?')}" if ticker else '--',worker_age)
    footer=Text(f"Workers {start+1}-{min(start+slots,state['workers'])}/{state['workers']} | page {page}/{pages} | monitor --page N\nCtrl+C: finish active checkpoints and stop. Rerun: verify receipts and resume.")
    if state.get('reason'):footer.append('\n'+state['reason'])
    return Group(summary,table,footer)


def run(root, folder, plan, budget):
    console=Console();progress={};rows={r['ticker']:dict(state=r['state'],reason=r.get('reason','')) for r in plan['rows']}
    pending=deque(r for r in plan['rows'] if r['state']=='queued');active={}
    with c.exclusive(folder/'controller.lock'):
        (folder/'STOP').unlink(missing_ok=True)
        state=dict(state='running',started_epoch=time.time(),updated_at=c.now(),workers=budget['workers'],
            sessions_total=sum(r.get('sessions',0) for r in plan['rows']),sessions_completed=0,
            rows=rows,progress=progress,retried=0,reused=0)
        c.write(folder/'executions'/f'{uuid.uuid4().hex}.json',dict(manifest_hash=plan['manifest_hash'],
            source_files=c.hashes(),scheduler_hashes=execution_hashes(),budget=budget,
            host=os.environ.get('COMPUTERNAME'),python=sys.executable,at=c.now()))
        old=signal.signal(signal.SIGINT,lambda *_:(folder/'STOP').touch())
        pool=ProcessPoolExecutor(max_workers=budget['workers'],mp_context=multiprocessing.get_context('spawn'),initializer=initialize_worker)
        last_plain=0
        try:
            with Live(console=console,auto_refresh=False,vertical_overflow='crop') as display:
                while pending or active:
                    if psutil.virtual_memory().available<2*GIB:
                        state['reason']='Free RAM below 2 GiB; finishing active checkpoints'
                        (folder/'STOP').touch()
                    stopping=(folder/'STOP').exists()
                    for slot,(row,future) in list(active.items()):
                        if not future.done():continue
                        ticker=row['ticker']
                        try:
                            result=future.result();progress[ticker]=result
                            rows[ticker].update(state=result['state'],reason=result.get('reason',''))
                            state['reused']+=int(result.get('reused',False))
                        except Exception as exc:
                            path=root/row['output']/'tickers'/row['directory']/'progress.json'
                            if path.exists():
                                saved=c.read(path)
                                if saved.get('updated_at','')>=rows[ticker]['started_at']:progress[ticker]=saved
                            rows[ticker].update(state='interrupted' if isinstance(exc,InterruptedError) else 'failed',reason=str(exc))
                        del active[slot]
                    for slot in range(budget['workers']):
                        if stopping or slot in active or not pending:continue
                        row=pending.popleft();rows[row['ticker']].update(state='active',slot=slot,started_at=c.now())
                        active[slot]=(row,pool.submit(execute,str(root),str(folder),row))
                    for row,_ in active.values():
                        path=root/row['output']/'tickers'/row['directory']/'progress.json'
                        if path.exists():
                            saved=c.read(path)
                            if saved.get('updated_at','')>=rows[row['ticker']]['started_at']:
                                progress[row['ticker']]=saved
                    state.update(state='stopping' if stopping else 'running',updated_at=c.now(),
                        sessions_completed=sum(p.get('completed',0) for p in progress.values()),
                        retried=sum(p.get('retried',0) for p in progress.values()))
                    c.write(folder/'status.json',state,immutable=False)
                    if console.is_terminal:display.update(render(state,console.width,console.height),refresh=True)
                    elif time.time()-last_plain>=15:
                        console.print(render(state,console.width,min(console.height,24)));last_plain=time.time()
                    if stopping and not active:break
                    if active:time.sleep(1)
            state['state']='interrupted' if (folder/'STOP').exists() else 'complete_with_gaps' if any(r['state'] in ('failed','deferred') for r in rows.values()) else 'complete'
        except BaseException as exc:
            state.update(state='failed',reason=str(exc) or type(exc).__name__)
            raise
        finally:
            if active:(folder/'STOP').touch()
            pool.shutdown(wait=True,cancel_futures=True)
            signal.signal(signal.SIGINT,old)
            for row,_ in active.values():rows[row['ticker']].update(state='interrupted',reason='Controller stopped; rerun to verify receipts')
            state['updated_at']=c.now();c.write(folder/'status.json',state,immutable=False)
            console.print(render(state,console.width,console.height))
            console.print(f"Full reasons and counts: {folder / 'status.json'}")
        return 1 if any(r['state']=='failed' for r in rows.values()) else 2 if state['state']!='complete' else 0


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('plan','preflight','run','status','monitor','stop'))
    parser.add_argument('--campaign',default=NAME,help='Immutable campaign name under the shared V7 root')
    parser.add_argument('--workers',type=int,help='Default: CPU/RAM budget, capped at 16; explicit maximum 60')
    parser.add_argument('--page',type=int,default=1)
    args=parser.parse_args()
    if not args.campaign or Path(args.campaign).name!=args.campaign or args.campaign in ('.','..'):
        raise ValueError('Campaign must be a single directory name')
    root=shared_root();folder=root/'filtered-preparation-campaigns'/args.campaign;console=Console()
    if args.command=='plan':
        if os.environ.get('COMPUTERNAME','').upper()==WORKSTATION_NAME:
            raise ValueError('Export the consumer contract from the laptop, then run on the workstation')
        plan=make_plan(root);c.write(folder/'plan.json',plan)
        counts=Counter(r['state'] for r in plan['rows'])
        console.print(f"Frozen {counts['queued']:,} tickers; {counts['deferred']:,} deferred; {sum(r.get('sessions',0) for r in plan['rows']):,} sessions")
        console.print(f"Consumer contract {plan['manifest_hash'][:16]} | {folder}")
        return 0
    if not (folder/'plan.json').exists():raise ValueError('Export the campaign plan from the laptop first')
    if args.command=='stop':
        (folder/'STOP').touch();console.print('Stop requested; workers finish active session checkpoints.');return 0
    if args.command in ('status','monitor'):
        with Live(console=console,auto_refresh=False,vertical_overflow='crop') as display:
            while True:
                state=c.read(folder/'status.json')
                if console.is_terminal:display.update(render(state,console.width,console.height,args.page),refresh=True)
                else:console.print(render(state,console.width,min(console.height,24),args.page))
                if args.command=='status' or state['state'] not in ('running','stopping'):break
                time.sleep(2 if console.is_terminal else 15)
        return 0
    plan=checked_plan(folder)
    if Catalog(root).fingerprint!=plan['catalog_hash']:raise ValueError('Published campaign authority changed; export a new named plan')
    maximum=resource_budget(os.cpu_count() or 1,psutil.virtual_memory().available)
    budget=resource_budget(os.cpu_count() or 1,psutil.virtual_memory().available,args.workers if args.workers is not None else min(16,maximum['maximum_workers']))
    c.load_env_files(c.discover_clickhouse_env_files(),verbose=False)
    if c.query('SELECT hostName() host')[0]['host'].upper()!=WORKSTATION_NAME:raise ValueError('ClickHouse is not the workstation authority')
    console.print(f"Pinned code/Python/NumPy/SciPy verified | {budget['workers']} processes | 1 SQL thread each")
    console.print(f"{budget['available_gib']} GiB free | {budget['admitted_memory_gib']} GiB admission budget | {folder}")
    if args.command=='preflight':return 0
    if os.environ.get('COMPUTERNAME','').upper()!=WORKSTATION_NAME:raise ValueError('Run this campaign on '+WORKSTATION_NAME)
    return run(root,folder,plan,budget)
