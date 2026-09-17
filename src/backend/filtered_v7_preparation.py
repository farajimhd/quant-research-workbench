"""Backtest orchestration of verified historical checkpoints, never live state."""
import asyncio
import os
from pathlib import Path
import subprocess
import sys


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
                process = await asyncio.create_subprocess_exec(sys.executable, '-B', '-m',
                    'research.level_book.v7.filtered_worker', '--runtime', str(folder), '--ticker', ticker,
                    cwd=str(Path(__file__).resolve().parents[2]), env=env,
                    stdout=log, stderr=log, creationflags=flags)
                try:
                    while process.returncode is None:
                        detail = 'Preparing filtered V7 history: '+ticker
                        progress = target/'progress.json'
                        if progress.exists():
                            p = await asyncio.to_thread(read, progress)
                            detail += f" - {p.get('completed', 0)}/{p.get('total', '?')} sessions; {p.get('stage', 'preparing')}"
                        await publish(completed, total, detail)
                        try:
                            await asyncio.wait_for(process.wait(), timeout=2)
                        except asyncio.TimeoutError:
                            pass
                    if process.returncode:
                        error = target/'error.json'
                        reason = (await asyncio.to_thread(read, error)).get('error') if error.exists() else 'see '+str(folder/'preparation.log')
                        raise ValueError(f'Filtered V7 preparation failed for {ticker}: {reason}')
                finally:
                    if process.returncode is None:
                        process.terminate()
                        await process.wait()
            for day in days:
                try:
                    book, _ = await asyncio.to_thread(catalog.select, ticker, str(day))
                except CoverageUnavailable:
                    continue
                if book.get('input_policy') != POLICY:
                    raise ValueError('Filtered V7 publication could not be verified for '+ticker)
        completed += 1
        await publish(completed, total, 'Filtered V7 histories checked')
