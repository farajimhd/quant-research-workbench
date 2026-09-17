"""Backtest orchestration of verified historical checkpoints, never live state."""
import asyncio
from contextlib import asynccontextmanager
import os
from pathlib import Path
import subprocess
import sys


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


async def prepare(tickers, days, publish):
    from src.market_engine.v7_catalog import Catalog, CoverageUnavailable
    from src.market_engine.filtered_v7_history import successor
    from src.market_engine.derived_trade_policy import POLICY
    from research.level_book.v7.campaign_store import read, write
    catalog = await asyncio.to_thread(Catalog)
    completed = 0
    total = len(tickers)
    await publish(completed, total, 'Checking filtered V7 histories')
    for ticker in tickers:
        needs = False
        for day in days:
            try:
                book, _ = await asyncio.to_thread(catalog.select, ticker, str(day))
                needs |= book.get('input_policy') != POLICY
            except CoverageUnavailable:
                # The QMD coverage report retains explicit identity/empty/source
                # exclusions. Never manufacture a seed or silently drop a ticker.
                continue
        if needs:
            candidates = catalog.sources(ticker)
            parent = next((p for _, p, _ in reversed(candidates) if p.get('input_policy') != POLICY), None)
            if parent is None:
                raise ValueError('No verified V7 parent history for '+ticker)
            folder, plan = successor(catalog.root, parent, ticker)
            await asyncio.to_thread(write, folder/'plan.json', plan)
            target = folder/'tickers'/plan['rows'][0]['directory']
            env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
            flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            with (folder/'preparation.log').open('ab') as log:
                async with preparation_process(sys.executable, '-B', '-m',
                    'research.level_book.v7.filtered_worker', '--runtime', str(folder), '--ticker', ticker,
                    cwd=str(Path(__file__).resolve().parents[2]), env=env,
                    stdout=log, stderr=log, creationflags=flags) as process:
                    while process.poll() is None:
                        detail = 'Preparing filtered V7 history: '+ticker
                        progress = target/'progress.json'
                        if progress.exists():
                            p = await asyncio.to_thread(read, progress)
                            detail += f" - {p.get('completed', 0)}/{p.get('total', '?')} sessions; {p.get('stage', 'preparing')}"
                        await publish(completed, total, detail)
                        await asyncio.sleep(2)
                    if process.returncode:
                        error = target/'error.json'
                        reason = (await asyncio.to_thread(read, error)).get('error') if error.exists() else 'see '+str(folder/'preparation.log')
                        raise ValueError(f'Filtered V7 preparation failed for {ticker}: {reason}')
            for day in days:
                try:
                    book, _ = await asyncio.to_thread(catalog.select, ticker, str(day))
                except CoverageUnavailable:
                    continue
                if book.get('input_policy') != POLICY:
                    raise ValueError('Filtered V7 publication could not be verified for '+ticker)
        completed += 1
        await publish(completed, total, 'Filtered V7 histories checked')
