"""Backtest orchestration of verified historical checkpoints, never live state."""
import asyncio
from contextlib import asynccontextmanager
import os
from pathlib import Path
import subprocess
import sys
from weakref import WeakKeyDictionary


_process_limits = WeakKeyDictionary()
_plan_locks = WeakKeyDictionary()


def process_limit():
    """Share the CPU/query budget across simultaneous runs on this API loop."""
    loop = asyncio.get_running_loop()
    if loop not in _process_limits:
        _process_limits[loop] = asyncio.Semaphore(4)
    return _process_limits[loop]


def _reap(process):
    """Do not leave a cancelled preparation writer running in the background."""
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


@asynccontextmanager
async def preparation_process(*args, **kwargs):
    # WindowsSelectorEventLoop deliberately serves the API, but has no asyncio
    # subprocess transport. Shield startup so cancellation cannot lose a child
    # that Popen is still creating in the worker thread.
    startup = asyncio.create_task(asyncio.to_thread(subprocess.Popen, args, **kwargs))
    process = None
    try:
        process = await asyncio.shield(startup)
        yield process
    finally:
        if process is None:
            process = await startup
        cleanup = asyncio.create_task(asyncio.to_thread(_reap, process))
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            await cleanup
            raise


async def prepare(tickers, days, publish, *, publish_details=None, workers=None,
                  verify_only=False):
    """Verify selected histories; optional campaign mode may build successors."""
    import time
    from datetime import datetime, timezone
    from copy import deepcopy
    from src.market_engine.v7_catalog import Catalog, CoverageUnavailable
    from src.market_engine.filtered_v7_history import successor
    from src.market_engine.derived_trade_policy import POLICY
    from research.level_book.v7.campaign_store import read, write
    names = list(dict.fromkeys(tickers))
    dates = sorted({str(day) for day in days})
    if not dates:
        raise ValueError('Filtered V7 preparation requires session dates')
    count = int(workers if workers is not None else os.environ.get('FILTERED_V7_WORKERS', '4'))
    if not 1 <= count <= 4:
        raise ValueError('FILTERED_V7_WORKERS must be between 1 and 4')
    catalog = await asyncio.to_thread(Catalog)
    started = time.monotonic()
    queue = asyncio.Queue()
    for ticker in names:
        queue.put_nowait(ticker)
    state = dict(total=len(names), completed=0, reused=0, built=0, unavailable=0, failed=0,
                 before=dates[-1], workers=[dict(slot=i+1,ticker=None,state='idle') for i in range(min(count,len(names)))])
    finished = asyncio.Event()
    def snapshot_progress():
        elapsed = time.monotonic()-started
        rate = state['completed']/elapsed if elapsed > 0 else 0
        snapshot = deepcopy(state)
        snapshot.update(active=sum(w['state'] in ('checking','building') for w in state['workers']),
            queued=queue.qsize()+sum(w['state']=='waiting' for w in state['workers']),elapsed_seconds=elapsed,tickers_per_minute=rate*60,
            eta_seconds=(len(names)-state['completed'])*elapsed/state['built'] if state['built'] >= 2 else None,
            updated_at=datetime.now(timezone.utc).isoformat())
        return snapshot
    async def report():
        if publish_details:
            await publish_details(snapshot_progress())
        await publish(state['completed'],len(names),'Preparing filtered V7 histories')
    async def monitor():
        while not finished.is_set():
            await report()
            try:
                await asyncio.wait_for(finished.wait(),timeout=.5)
            except asyncio.TimeoutError:
                pass
    def inspect(ticker):
        needs=False;eligible=False
        for day in dates:
            try:
                book,_=catalog.select(ticker,day)
                eligible=True
                needs |= book.get('input_policy') != POLICY
            except CoverageUnavailable:
                continue
        return needs,eligible
    async def consume(slot):
        while not queue.empty():
            ticker=queue.get_nowait()
            slot_id = slot['slot']
            slot.clear();slot.update(slot=slot_id,ticker=ticker,state='checking',updated_at=datetime.now(timezone.utc).isoformat())
            try:
                needs,eligible=await asyncio.to_thread(inspect,ticker)
                if needs and verify_only:
                    # Backtest preparation never creates a new level-book
                    # history. Coverage preflight excludes this ticker below.
                    state['unavailable']+=1
                elif needs:
                    candidates=await asyncio.to_thread(catalog.sources,ticker)
                    parent=next((p for _,p,_ in reversed(candidates) if p.get('input_policy') != POLICY),None)
                    if parent is None:
                        raise ValueError('No verified V7 parent history for '+ticker)
                    folder,plan=successor(catalog.root,parent,ticker)
                    locks = _plan_locks.setdefault(asyncio.get_running_loop(), {})
                    async with locks.setdefault(str(folder), asyncio.Lock()):
                        await asyncio.to_thread(write,folder/'plan.json',plan)
                    target=folder/'tickers'/plan['rows'][0]['directory']
                    slot.update(state='waiting',stage='Waiting for worker capacity',completed=0,total=None)
                    flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
                    with (folder/'preparation.log').open('ab') as log:
                        async with process_limit():
                            slot.update(state='building',stage='Starting worker')
                            async with preparation_process(sys.executable,'-B','-m','research.level_book.v7.filtered_worker',
                                    '--before',dates[-1],'--runtime',str(folder),'--ticker',ticker,
                                    cwd=str(Path(__file__).resolve().parents[2]),
                                    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'),stdout=log,stderr=log,creationflags=flags) as process:
                                while process.poll() is None:
                                    if (target/'progress.json').exists():
                                        p=await asyncio.to_thread(read,target/'progress.json')
                                        # Do not display a previous invocation's failed progress during startup.
                                        if p.get('pid') == process.pid:
                                            slot.update({k:p.get(k) for k in ('stage','completed','total','session','retried','resumed','updated_at')})
                                    await asyncio.sleep(.25)
                                if (target/'progress.json').exists():
                                    p=await asyncio.to_thread(read,target/'progress.json')
                                    if p.get('pid') == process.pid:
                                        slot.update({k:p.get(k) for k in ('stage','completed','total','session','retried','resumed','updated_at')})
                                if process.returncode:
                                    error=target/'error.json'
                                    reason=(await asyncio.to_thread(read,error)).get('error') if error.exists() else 'see '+str(folder/'preparation.log')
                                    raise ValueError(f'Filtered V7 preparation failed for {ticker}: {reason}')
                    remaining,_=await asyncio.to_thread(inspect,ticker)
                    if remaining:
                        raise ValueError('Filtered V7 publication could not be verified for '+ticker)
                    state['built']+=1
                elif eligible:
                    state['reused']+=1
                else:
                    state['unavailable']+=1
                state['completed']+=1
                slot.update(state='complete',updated_at=datetime.now(timezone.utc).isoformat())
            except BaseException as exc:
                slot.update(state='cancelled' if isinstance(exc,asyncio.CancelledError) else 'failed',error=str(exc))
                if not isinstance(exc,asyncio.CancelledError):state['failed']+=1
                raise
            finally:
                queue.task_done()
    tasks=[asyncio.create_task(consume(slot)) for slot in state['workers']]
    ticker_tasks=list(tasks)
    tasks.append(asyncio.create_task(monitor()))
    async def finish_workers():
        await asyncio.gather(*ticker_tasks)
        finished.set()
    completion=asyncio.create_task(finish_workers())
    try:
        await asyncio.gather(completion,tasks[-1])
        await report()
    finally:
        finished.set()
        for task in [completion,*tasks]:
            if not task.done():task.cancel()
        await asyncio.gather(completion,*tasks,return_exceptions=True)
        if publish_details:
            await publish_details(snapshot_progress())
