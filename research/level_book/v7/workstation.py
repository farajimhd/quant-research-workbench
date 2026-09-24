"""Workstation scheduling; the frozen campaign worker remains the calculation authority.

The laptop controller and this controller share the same cross-host locks, plan,
receipts and STOP boundary. No checkpoint or numerical-version migration occurs.
"""
import argparse
from collections import Counter, deque
from concurrent.futures import ProcessPoolExecutor
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime
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
from rich.panel import Panel
from rich.progress_bar import ProgressBar
from rich.table import Table
from rich.text import Text

from . import campaign as c
from src.runtime_paths import WORKSTATION_NAME, WORKSTATION_RUNTIME_ROOT

DEFAULT_NAME = 'all-tradable-20250101-20260912-mle-reporting-v1'
EXECUTION_FILES = ('research/level_book/v7/workstation.py', 'scripts/run_level_book_v7_workstation.py')
GIB = 1024 ** 3


def resource_budget(cpus, available_bytes, workers=None, threads=1):
    """Reserve 1/4 of free RAM and 1/8 of CPUs for the OS/other services.

Two GiB per slot budgets one GiB for its read-only SQL query and one GiB for
Python/book state. This is admission budgeting, not an OS memory hard limit.
Windows ProcessPoolExecutor permits at most 61 children; keep one slot spare.
    """
    if threads not in (1, 2):
        raise ValueError('Use one or two ClickHouse threads per worker')
    cpu_budget = max(0, cpus - max(2, (cpus + 7) // 8))
    memory_slots = int(available_bytes * .75 // (2 * GIB))
    maximum = min(60, cpu_budget // (1 + threads), memory_slots, 128 // threads)
    if maximum < 1:
        raise ValueError('Insufficient free CPU/RAM for a worker and its SQL query')
    chosen = maximum if workers is None else workers
    if not 1 <= chosen <= maximum:
        raise ValueError(f'Requested {chosen} workers; current CPU/RAM budget permits 1..{maximum}')
    return dict(workers=chosen, threads=threads, logical_cpus=cpus,
                available_gib=round(available_bytes / GIB, 2), query_threads=chosen * threads,
                admitted_memory_gib=chosen * 2, maximum_workers=maximum)


def runtime_path(raw=None):
    # On the workstation use the local alias of the same shared directory.
    # Avoid SMB for millions of checkpoints; do not copy/fork campaign state.
    workstation = os.environ.get('COMPUTERNAME', '').upper() == WORKSTATION_NAME
    base = c.ROOT if workstation else WORKSTATION_RUNTIME_ROOT / 'level-book-v7'
    path = Path(raw) if raw else base / DEFAULT_NAME
    if workstation:
        try:
            path = c.ROOT / path.relative_to(WORKSTATION_RUNTIME_ROOT / 'level-book-v7')
        except ValueError:
            pass
    path = path.resolve()
    if not base.is_dir() or not path.is_relative_to(base.resolve()):
        raise ValueError(f'Campaign must reside under the available runtime root {base}')
    if not (path / 'plan.json').is_file():
        raise ValueError('No frozen plan here. This runner resumes an existing V7 campaign; it does not replan.')
    return path


def execution_hashes():
    return {name: sha256((c.REPO / name).read_bytes()).hexdigest() for name in EXECUTION_FILES}


def initialize_worker():
    # Only the controller interprets Ctrl+C. Workers finish their active day
    # after the controller publishes STOP. Abrupt host/process loss still uses
    # the campaign's receipt verification on restart.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    c.load_env_files(c.discover_clickhouse_env_files(), verbose=False)


def execute_ticker(root, ticker, threads, execution_file):
    """One small task per ticker; pool processes and HTTP connections are reused."""
    root = Path(root)
    execution = c.read(Path(execution_file))
    if execution['scheduler_hashes'] != execution_hashes():
        raise ValueError('Workstation scheduler changed during execution')
    if execution['plan_hash'] != c.checked_plan(root)['plan_hash']:
        raise ValueError('Worker execution belongs to a different plan')
    target = c.paths(root, ticker)
    target.mkdir(parents=True, exist_ok=True)
    with (target / 'worker.log').open('a', encoding='utf-8', buffering=1) as log:
        with redirect_stdout(log), redirect_stderr(log):
            try:
                c.worker(SimpleNamespace(runtime=root, ticker=ticker, threads=threads))
            finally:
                # A laptop worker exits after a ticker. Persistent workers must
                # explicitly release the old ticker's observation-keyed cache.
                from src.market_engine.reaction_band import cached_fit
                cached_fit.cache_clear()
                gc.collect()
    return c.read(target / 'progress.json')


def restore_progress(root, plan, retry_failed=False):
    progress = c.load_progress(root, plan)
    rows = {}
    for row in plan['rows']:
        ticker = row['ticker']
        saved = progress.get(ticker, {})
        state = row['status']
        if state != 'deferred' and saved.get('state') == 'complete':
            ready = c.read(c.paths(root, ticker) / 'ready.json')
            if ready['plan_hash'] != plan['plan_hash']:
                raise ValueError(f'{ticker}: completed ticker belongs to another plan')
            if ready['checkpoint_hash']:
                book = c.verified_book(c.paths(root, ticker) / 'books' / f"{ready['book_session']}.json.gz")
                if book['checkpoint_hash'] != ready['checkpoint_hash']:
                    raise ValueError(f'{ticker}: completed checkpoint hash mismatch')
            state = 'complete'
        elif state != 'deferred' and saved.get('state') == 'failed' and not retry_failed:
            state = 'failed'
        rows[ticker] = dict(state=state, reason=saved.get('reason') or row['reason'])
    return rows, progress


def render(state, width=110, monitor_only=False):
    # Match the V6 Rich dashboard's overall panel and per-worker progress bars,
    # but include every slot: wider terminals use two side-by-side tables.
    active = {r['slot']: t for t, r in state['rows'].items() if r['state'] == 'active'}
    progress = state.get('worker_progress', {})
    counts = Counter(r['state'] for r in state['rows'].values())
    done, total = state['sessions_completed'], state['sessions_total']
    rate = max(0, done - state['initial_completed']) / max(1, time.time() - state['started_epoch'])
    age = time.time() - datetime.fromisoformat(state['updated_at']).timestamp()
    eta = (total - done) / rate if rate else None
    summary = Text(f"{state['state'].upper()} | {done:,}/{total:,} sessions ({done / max(1, total):.1%})\n")
    summary.append(
        f"Active {counts['active']} | queued {counts['queued']} | complete {counts['complete']} | deferred {counts['deferred']}\n"
        f"Failed {counts['failed']} | interrupted {counts['interrupted']} | retries {state.get('retried', 0)} | controller age {age:.0f}s\n"
        f"{rate * 60:.1f} sessions/min | ETA {c.duration(eta)} (mixed workload)")
    overall = Panel(Group(summary, ProgressBar(total=max(1, total), completed=done)),
                    title='V7 MLE book • campaign progress', border_style='cyan')
    columns = 2 if width >= 140 else 1
    capacity = (state['workers'] + columns - 1) // columns
    grid = Table.grid(expand=True, padding=(0, 1))
    for _ in range(columns):
        grid.add_column(ratio=1)
    tables = []
    compact = width // columns < 100
    for column in range(columns):
        table = Table(expand=True, box=None, padding=(0, 1))
        for name in ('ID', 'Ticker', 'Session', 'Stage', 'Done', 'Progress', 'Age'):
            table.add_column(name, no_wrap=True, overflow='ellipsis')
        for slot in range(column * capacity, min((column + 1) * capacity, state['workers'])):
            ticker = active.get(slot)
            value = progress.get(ticker, {})
            worker_age = time.time() - datetime.fromisoformat(value['updated_at']).timestamp() if value.get('updated_at') else None
            worker_done, worker_total = value.get('completed', 0), value.get('total', 0)
            bar = Table.grid(padding=(0, 1))
            if ticker and worker_total:
                bar.add_row(ProgressBar(total=worker_total, completed=min(worker_done, worker_total), width=6 if compact else 8),
                            Text(f'{min(worker_done, worker_total) / worker_total:.0%}'))
            stage = value.get('stage', 'starting' if ticker else 'idle')
            if compact:
                stage = {'ClickHouse OHLCV': 'SQL', 'MLE fitting': 'MLE', 'checkpoint saved': 'saved'}.get(stage, stage)
            table.add_row(f'{slot + 1:02}', ticker or '--', value.get('session', '--'), stage,
                          f'{worker_done}/{worker_total or "?"}' if ticker else '--', bar,
                          f'{worker_age:.0f}s' if worker_age is not None else '--')
        tables.append(table)
    grid.add_row(*tables)
    action = 'close monitor; campaign continues' if monitor_only else 'finish active checkpoints and stop'
    footer = Text(f"All {state['workers']} worker slots | Ctrl+C: {action}")
    if state.get('stop_reason'):
        footer.append('\n' + state['stop_reason'])
    return Group(overall, Panel(grid, title='Workers • completed sessions', padding=(0, 0)), footer)


def run(root, plan, budget, retry_failed=False, take_over=False):
    # Source/runtime checks occur before a takeover can stop the old controller.
    if take_over:
        (root / 'STOP').touch()
    console = Console()
    lock = c.exclusive(root / 'controller.lock')
    deadline = time.monotonic() + 600
    announced = False
    while True:
        try:
            lock.__enter__()
            break
        except OSError:
            if not take_over or time.monotonic() >= deadline:
                raise ValueError('Campaign controller is active. Use --take-over to request a checkpoint-safe handoff.') from None
            if not announced:
                console.print('Waiting for the current controller to finish its active checkpoints…')
                announced = True
            time.sleep(5)
            lock = c.exclusive(root / 'controller.lock')
    try:
        # A controller crash can leave workers alive. Never clear STOP or steal
        # their ticker ownership while any old worker still holds a lock.
        for row in plan['rows']:
            if row['status'] == 'deferred':
                continue
            path = c.paths(root, row['ticker']) / 'worker.lock'
            if path.exists():
                with c.exclusive(path):
                    pass
        rows, progress = restore_progress(root, plan, retry_failed)
        execution = dict(version='v7-workstation-execution-1', plan_hash=plan['plan_hash'], created_at=c.now(),
                         scheduler_hashes=execution_hashes(), budget=budget, host=os.environ.get('COMPUTERNAME'),
                         pid=os.getpid(), python=sys.executable, runtime=str(root), source_files=c.hashes(), software=plan['software'])
        execution_file = root / 'executions' / f'{uuid.uuid4().hex}.json'
        c.write(execution_file, execution)
        (root / 'STOP').unlink(missing_ok=True)
        state = dict(state='running', pid=os.getpid(), started_epoch=time.time(), updated_at=c.now(),
                     workers=budget['workers'], threads=budget['threads'], budget=budget, execution_file=str(execution_file), rows=rows,
                     sessions_total=sum((r['coverage'] or {}).get('days', 0) for r in plan['rows'] if r['status'] != 'deferred'),
                     initial_completed=sum(p.get('completed', 0) for p in progress.values()))
        # Frozen order is largest tickers first. At most one outstanding task
        # per slot; no unbounded executor queue or one process per queued ticker.
        pending = deque(r['ticker'] for r in plan['rows'] if rows[r['ticker']]['state'] == 'queued')
        active = {}
        completed_counts = {t: p.get('completed', 0) for t, p in progress.items()}
        old = signal.signal(signal.SIGINT, lambda *_: (root / 'STOP').touch())
        pool = ProcessPoolExecutor(max_workers=budget['workers'], mp_context=multiprocessing.get_context('spawn'), initializer=initialize_worker)
        last_plain = 0
        fatal = None
        try:
            with Live(console=console, auto_refresh=False, transient=False, vertical_overflow='visible') as display:
                while True:
                    if psutil.virtual_memory().available < 2 * GIB:
                        state['stop_reason'] = 'Free RAM below 2 GiB: stopping after active checkpoints'
                        (root / 'STOP').touch()
                    stopping = (root / 'STOP').exists()
                    for slot, (ticker, future) in list(active.items()):
                        if not future.done():
                            continue
                        try:
                            saved = future.result()
                            rows[ticker].update(state=saved['state'], reason=saved.get('reason', ''))
                        except Exception as exc:
                            saved = c.read(c.paths(root, ticker) / 'progress.json') if (c.paths(root, ticker) / 'progress.json').exists() else {}
                            rows[ticker].update(state='failed', reason=str(exc))
                        progress[ticker] = saved
                        completed_counts[ticker] = max(completed_counts.get(ticker, 0), saved.get('completed', 0))
                        del active[slot]
                    for slot in range(budget['workers']):
                        if stopping or slot in active or not pending:
                            continue
                        ticker = pending.popleft()
                        future = pool.submit(execute_ticker, str(root), ticker, budget['threads'], str(execution_file))
                        active[slot] = (ticker, future)
                        rows[ticker].update(state='active', slot=slot)
                    for ticker, _ in active.values():
                        path = c.paths(root, ticker) / 'progress.json'
                        if path.exists():
                            progress[ticker] = c.read(path)
                            # The worker re-verifies receipts on resume; never
                            # make the displayed durable count go backwards.
                            completed_counts[ticker] = max(completed_counts.get(ticker, 0), progress[ticker].get('completed', 0))
                    state.update(state='stopping' if stopping else 'running', updated_at=c.now(),
                                 sessions_completed=sum(completed_counts.values()), retried=sum(p.get('retried', 0) for p in progress.values()),
                                 worker_progress={t: progress[t] for t, _ in active.values() if t in progress})
                    c.write(root / 'status.json', state, immutable=False)
                    if console.is_terminal:
                        display.update(render(state, console.width), refresh=True)
                    elif time.time() - last_plain >= 15:
                        console.print(f"{state['state']} | sessions {state['sessions_completed']:,}/{state['sessions_total']:,} | {dict(Counter(r['state'] for r in rows.values()))}")
                        last_plain = time.time()
                    if not active:
                        break
                    time.sleep(2)
            state['state'] = 'interrupted' if stopping else 'complete_with_gaps' if any(r['state'] in ('failed', 'deferred') for r in rows.values()) else 'complete'
            state['updated_at'] = c.now()
            c.write(root / 'status.json', state, immutable=False)
            console.print(render(state, console.width))
        except BaseException as exc:
            fatal = str(exc) or type(exc).__name__
            raise
        finally:
            if active:
                (root / 'STOP').touch()
                console.print('Waiting for workers to finish active checkpoints…')
            pool.shutdown(wait=True, cancel_futures=True)
            signal.signal(signal.SIGINT, old)
            if fatal is not None:
                for ticker, _ in active.values():
                    path = c.paths(root, ticker) / 'progress.json'
                    saved = c.read(path) if path.exists() else {}
                    rows[ticker].update(state=saved.get('state', 'interrupted'), reason=saved.get('reason', fatal))
                    if rows[ticker]['state'] == 'active':
                        rows[ticker]['state'] = 'interrupted'
                    completed_counts[ticker] = max(completed_counts.get(ticker, 0), saved.get('completed', 0))
                state.update(state='failed', reason=fatal, updated_at=c.now(), sessions_completed=sum(completed_counts.values()))
                c.write(root / 'status.json', state, immutable=False)
        if any(r['state'] == 'failed' for r in rows.values()):
            raise SystemExit(1)
    finally:
        lock.__exit__(None, None, None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('preflight', 'run', 'status', 'monitor', 'stop'), nargs='?', default='preflight')
    parser.add_argument('--runtime', type=Path)
    parser.add_argument('--workers', type=int, help='Default: CPU/RAM-derived, at most 60')
    parser.add_argument('--threads', type=int, default=1, help='ClickHouse threads per worker (1 or 2)')
    parser.add_argument('--retry-failed', action='store_true')
    parser.add_argument('--take-over', action='store_true', help='Stop the old controller at a checkpoint before resuming here')
    args = parser.parse_args()
    root = runtime_path(args.runtime)
    console = Console()
    if args.command == 'stop':
        (root / 'STOP').touch()
        console.print('Stop requested. Workers finish active session checkpoints.')
        return
    if args.command in ('status', 'monitor'):
        with Live(console=console, auto_refresh=False, vertical_overflow='visible') as live:
            while True:
                state = c.read(root / 'status.json')
                if console.is_terminal:
                    live.update(render(state, console.width, monitor_only=True), refresh=True)
                else:
                    console.print(render(state, console.width, monitor_only=True))
                if args.command == 'status' or state['state'] not in ('running', 'stopping'):
                    break
                time.sleep(3 if console.is_terminal else 15)
        return
    if os.environ.get('COMPUTERNAME', '').upper() != WORKSTATION_NAME:
        raise ValueError(f'Run preflight/run on {WORKSTATION_NAME}; monitor/stop may run on the laptop')
    plan = c.checked_plan(root)
    budget = resource_budget(os.cpu_count() or 1, psutil.virtual_memory().available, args.workers, args.threads)
    c.load_env_files(c.discover_clickhouse_env_files(), verbose=False)
    if c.query('SELECT hostName() host')[0]['host'].upper() != WORKSTATION_NAME:
        raise ValueError('ClickHouse is not the pinned workstation host')
    console.print(f"Verified frozen plan {plan['plan_hash'][:12]} | {budget['logical_cpus']} logical CPUs | {budget['available_gib']:.1f} GiB free")
    console.print(f"{budget['workers']} persistent processes | {budget['threads']} SQL threads each | {budget['admitted_memory_gib']} GiB admission budget")
    console.print(f'Local checkpoints: {root}')
    console.print('MLE code and numerical runtime match the existing campaign. No checkpoint rebuild required.')
    if args.command == 'run':
        run(root, plan, budget, args.retry_failed, args.take_over)


if __name__ == '__main__':
    main()
