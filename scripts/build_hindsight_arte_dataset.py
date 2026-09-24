"""Build versioned Phase 1 and Phase 2 hindsight datasets from certified arte tables.

python -B scripts/build_hindsight_arte_dataset.py preflight --date 2026-08-21
python -B scripts/build_hindsight_arte_dataset.py benchmark --date 2026-08-21 --tickers AAPL SUGP
python -B scripts/build_hindsight_arte_dataset.py run --start 2026-08-18 --end 2026-09-18
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
os.environ.setdefault('POLARS_MAX_THREADS', '2')
import sys
sys.dont_write_bytecode = True
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import argparse
from datetime import date, datetime, time, timezone
from hashlib import sha256
import math
import signal
from time import monotonic

import polars as pl
from rich.console import Console
from research.mlops.env import load_env_files
from research.mlops.clickhouse import discover_clickhouse_env_files
from src.data_provider.calendar import market_sessions
from src.runtime_paths import runtime_root, WORKSTATION_NAME
from src.market_engine.hindsight_phase1 import bounds, digest, NY
from src.market_engine.hindsight_batch import ordered_jobs, worker_budget
from src.market_engine.level_book_store import read, write
from src.market_engine import hindsight_arte as labels
from src.market_engine import hindsight_arte_source as source_api
from scripts.build_hindsight_phase1 import exclusive, file_hash, verified
from scripts.build_hindsight_greedy import parquet
from scripts.build_hindsight_dataset import STOP, phase2_process

SOURCES = ('scripts/build_hindsight_arte_dataset.py','src/market_engine/hindsight_arte.py',
    'src/market_engine/hindsight_arte_source.py','src/market_engine/hindsight_phase1.py',
    'src/market_engine/hindsight_batch.py','scripts/build_hindsight_phase1.py',
    'scripts/build_hindsight_dataset.py','scripts/build_hindsight_greedy.py',
    'src/market_engine/hindsight_greedy.py','src/market_engine/hindsight_phase1_source.py',
    'src/market_engine/level_book_store.py','pipelines/market_sip/events/market_day_sql.py')


def stopped(root):
    return STOP.is_set() or (root/'STOP').exists()


def listing_work(listing, day, source, plan, root, threads):
    started = monotonic()
    folder = root/'listings'/digest(listing)[:20]
    folder.mkdir(parents=True, exist_ok=True)
    ticker = listing['ticker']
    c = source_api.reader(threads)
    stage = 'initial integrity'
    try:
        source_api.verify_listing(c,source,day,ticker)
        if (folder/'ready.json').exists():
            ready = read(folder/'ready.json')
            if ready['plan_hash'] != plan['plan_hash'] or ready['listing'] != listing:
                raise ValueError('Saved listing provenance changed')
            verified(folder,ready)
            return dict(ticker=ticker,status='reused',rows=ready['rows'],coverage=ready['coverage'],elapsed_seconds=monotonic()-started)
        verified_at = monotonic()
        stage = 'bars and indicators'
        bars, indicators = source_api.inputs(c,source,day,ticker)
        left, right = bounds(day)
        for frame in (bars,indicators):
            if frame.filter(~pl.col('time_us').is_between(left+1,right)).height:
                raise ValueError('Persisted row outside completed session')
        stage = 'MACD targets'
        cutoff = plan['liquidation_us']
        bars = bars.filter(pl.col('time_us') <= cutoff)
        indicators = indicators.filter(pl.col('time_us') <= cutoff)
        episodes = labels.intervals(indicators,cutoff)
        target = labels.targets(bars,episodes,plan['lookback_seconds'])
        stage = 'price-action labels'
        values = labels.decision_values(day,bars,target['positions'],liquidation_us=cutoff).with_columns(
            pl.lit(ticker).alias('ticker'),pl.lit(listing['listing_id']).alias('listing_id'))
        terminal = values.filter(pl.col('time_us') == cutoff).row(0,named=True)
        target['terminal_liquidation'] = dict(time_us=cutoff,price=terminal['decision_price'],
            price_us=terminal['price_us'],basis='latest_completed_trade_close_at_or_before_cutoff')
        # Recheck pinned products before publishing; do not trust a mutable latest pointer.
        stage = 'final integrity'
        source_api.verify_listing(c,source,day,ticker)
        coverage = labels.label_coverage(values)
        parquet(folder/'opportunities.parquet',values)
        write(folder/'targets.json',target)
        files = {name:file_hash(folder/name) for name in ('opportunities.parquet','targets.json')}
        ready = dict(plan_hash=plan['plan_hash'],listing=listing,rows=values.height,files=files,coverage=coverage,
            timings=dict(initial_integrity_seconds=verified_at-started,extraction_seconds=monotonic()-verified_at))
        write(folder/'ready.json',ready)
        return dict(ticker=ticker,status='completed',rows=values.height,coverage=coverage,elapsed_seconds=monotonic()-started)
    except Exception as exc:
        raise RuntimeError(f'{stage}: {exc}') from exc
    finally:
        c.close()


def phase1(day, source, listings, population, root, args, console, code):
    folder = root/'days'/str(day)/'phase1'
    folder.mkdir(parents=True,exist_ok=True)
    plan = dict(version=labels.VERSION,date=str(day),selected=listings,
        scope='explicit_canary' if args.tickers else 'certified_market_day_build_population',
        source_build_id=source['build_id'],source_definition_hash=source['definition_hash'],
        source_units=source['units'][str(day)],population=population,
        lookback_seconds=args.lookback_seconds,code_hashes={k:v for k,v in code.items() if 'greedy' not in k},
        polars_version=pl.__version__,target_clock='completed_100ms_bar_end',
        valuation_basis='price_action',
        liquidation_us=labels.liquidation_time(day),
        liquidation_seconds_before_close=labels.LIQUIDATION_SECONDS_BEFORE_CLOSE,
        session_close='20:00 America/New_York',
        price_policy='latest completed eligible 100ms trade close; retain observation age; no quote gates',
        volume_policy='completed_1s_canonical_volume; rolling_10s; cumulative_from_0400',
        semantics='Local MACD swing supervision; retained target and reward selection; no event parity claim')
    plan['plan_hash'] = digest(plan)
    counts = dict(completed=0,reused=0,failed=0)
    results = []
    started = monotonic()
    last_log = [0.]
    def heartbeat(submitted,active,waiting):
        state = dict(counts=counts,active=active,queued=len(listings)-submitted,awaiting_reduction=waiting,retried=0)
        write(folder/'progress.json',state,immutable=False)
        if monotonic()-last_log[0] > 10:
            console.print(f'{day} Phase 1 | completed {counts["completed"]} | reused {counts["reused"]} | active {active} | queued {state["queued"]} | failed {counts["failed"]} | {monotonic()-started:.1f}s')
            last_log[0] = monotonic()
    with exclusive(folder/'run.lock'):
        write(folder/'plan.json',plan)
        (folder/'complete.json').unlink(missing_ok=True)
        jobs = ordered_jobs(listings,lambda row:listing_work(row,day,source,plan,folder,args.query_threads),
            args.workers,lambda:stopped(root) or stopped(folder),heartbeat)
        for listing, result in jobs:
            if isinstance(result,Exception):
                result = dict(ticker=listing['ticker'],status='failed',error=str(result))
                console.print(f'Failed {listing["ticker"]}: {result["error"]}',markup=False)
            counts[result['status']] += 1
            results.append(result)
        complete = len(results) == len(listings) and not counts['failed']
        state = 'complete' if complete else 'failed' if counts['failed'] else 'interrupted'
        summary = dict(status=state,counts=counts,results=results,elapsed_seconds=monotonic()-started)
        write(folder/'summary.json',summary,immutable=False)
        write(folder/'progress.json',dict(status=state,counts=counts,active=0,queued=len(listings)-len(results),retried=0),immutable=False)
        if complete:
            write(folder/'complete.json',dict(plan_hash=plan['plan_hash'],listing_count=len(results),rows=sum(r['rows'] for r in results)))
        console.print(f'{day} Phase 1: {state} | completed {counts["completed"]}, reused {counts["reused"]}, failed {counts["failed"]}')
    return folder, complete


def market_coverage(root):
    result = {}
    for mode in ('long','short','long_short'):
        values = pl.read_parquet(root/f'{mode}.parquet')
        result[mode] = dict(seconds=values.height,available_seconds=values.filter(pl.col('status') == 'available').height,
            seconds_with_candidates=values.filter(pl.col('priced_candidates') > 0).height,
            available_seconds_with_candidates=values.filter((pl.col('status') == 'available') & (pl.col('priced_candidates') > 0)).height,
            unavailable_seconds=values.filter(pl.col('status') != 'available').height,
            status_counts=values.group_by('status').len().to_dicts(),
            hourly=values.with_columns(pl.from_epoch('time_us',time_unit='us').dt.replace_time_zone('UTC')
                .dt.convert_time_zone('America/New_York').dt.hour().alias('hour_ny')).group_by('hour_ny').agg(
                    pl.len().alias('seconds'),(pl.col('status') == 'available').sum().alias('available_seconds'),
                    (pl.col('priced_candidates') > 0).sum().alias('seconds_with_candidates')).sort('hour_ny').to_dicts())
    return result


def main(argv=None):
    invocation_started = monotonic()
    parser = argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command',choices=('preflight','benchmark','run'))
    parser.add_argument('--manifest',type=Path,help='Completed market-day manifest; default: runtime/market-day/latest.json')
    parser.add_argument('--ledger',type=Path,help='Read-only certification ledger; default: manifest parent runtime/build-ledger-v2.sqlite3')
    parser.add_argument('--date',type=date.fromisoformat)
    parser.add_argument('--start',type=date.fromisoformat)
    parser.add_argument('--end',type=date.fromisoformat)
    parser.add_argument('--tickers',nargs='+',help='Explicit canary subset; never treated as full market')
    parser.add_argument('--workers',type=int,default=None)
    parser.add_argument('--query-threads',type=int,choices=range(1,5),default=2)
    parser.add_argument('--lookback-seconds',type=int,choices=range(31),default=2)
    parser.add_argument('--gamma',type=float,default=.99)
    parser.add_argument('--cost-per-share',type=float,default=0.)
    args = parser.parse_args(argv)
    if not math.isfinite(args.gamma) or not 0 < args.gamma <= 1 or not math.isfinite(args.cost_per_share) or args.cost_per_share < 0:
        parser.error('gamma must be in (0,1]; cost must be finite and nonnegative')
    if args.date and (args.start or args.end):
        parser.error('Use --date OR --start and --end')
    if not args.date and not (args.start and args.end):
        parser.error('Supply --date or both --start and --end')
    start,end = (args.date,args.date) if args.date else (args.start,args.end)
    if start > end:
        parser.error('start must not follow end')
    days = market_sessions(start,end)
    if not days or datetime.combine(days[-1],time(20),NY) > datetime.now(timezone.utc):
        parser.error('Require completed XNYS sessions')
    if args.command == 'benchmark' and not args.tickers:
        parser.error('benchmark requires explicit --tickers')
    if os.environ.get('COMPUTERNAME','').upper() == WORKSTATION_NAME and not os.environ.get('QW_RUNTIME_ROOT'):
        os.environ['QW_RUNTIME_ROOT'] = 'D:/TradingML/runtimes'
    runtime = runtime_root().resolve()
    if not runtime.is_dir():
        raise ValueError('Required runtime root is unavailable')
    args.workers = worker_budget(args.workers,threads=args.query_threads)
    args.tickers = sorted(set(t.upper() for t in args.tickers)) if args.tickers else None
    manifest = args.manifest or runtime/'market-day/latest.json'
    ledger = args.ledger or manifest.parent.parent/'build-ledger-v2.sqlite3'
    console = Console()
    console.print('Checking completed arte build, population and storage certificates...')
    source = source_api.load_build(manifest,ledger,days,args.tickers)
    load_env_files(discover_clickhouse_env_files(),verbose=False)
    c = source_api.reader(args.query_threads)
    try:
        source_api.storage_check(c)
        populations = {str(day):source_api.population(c,source,day) for day in days}
    finally:
        c.close()
    for day in days:
        rows,pop = populations[str(day)]
        console.print(f'{day}: {len(rows):,} tickers | universe {pop["certificate"]["status"]} | certified source build')
    preflight_seconds = monotonic()-invocation_started
    if args.command == 'preflight':
        console.print('Preflight passed. Product checksums are rechecked per listing during extraction. No dataset written.')
        return 0
    code = {p:sha256((REPO/p).read_text(encoding='utf-8').replace('\r\n','\n').encode()).hexdigest() for p in SOURCES}
    plan = dict(version=labels.VERSION,source_build_id=source['build_id'],source_definition_hash=source['definition_hash'],
        source_units_hash=digest(source['units']),
        dates=list(map(str,days)),tickers=args.tickers,lookback_seconds=args.lookback_seconds,
        gamma=args.gamma,cost_per_share=args.cost_per_share,code_hashes=code,polars_version=pl.__version__)
    plan['plan_hash'] = digest(plan)
    root = runtime/'hindsight-arte'/plan['plan_hash'][:20]
    root.mkdir(parents=True,exist_ok=True)
    console.print('Artifacts: '+str(root),soft_wrap=True)
    console.print(f'{args.workers} listing workers | one session at a time | Ctrl+C/STOP drains active work | retries 0')
    STOP.clear()
    previous = signal.signal(signal.SIGINT,lambda *_:STOP.set())
    results = []
    started = monotonic()
    try:
        with exclusive(root/'run.lock'):
            write(root/'plan.json',plan)
            (root/'complete.json').unlink(missing_ok=True)
            for day in days:
                if stopped(root):
                    break
                rows,pop = populations[str(day)]
                p1,complete = phase1(day,source,rows,pop,root,args,console,code)
                item = dict(date=str(day),phase1_root=str(p1),status='failed')
                if complete and not stopped(root):
                    folder = p1.parent
                    handoff = folder/'phase2-location.json'
                    command = ['scripts/build_hindsight_greedy.py','build','--phase1',str(p1),'--workers',str(args.workers),
                        '--gamma',str(args.gamma),'--cost-per-share',str(args.cost_per_share),'--result-file',str(handoff)]
                    last = [0.]
                    def progress(message):
                        if monotonic()-last[0] > 10:
                            console.print(f'{day} {message}')
                            last[0] = monotonic()
                    result = phase2_process(command,folder,root,handoff,progress)
                    item['phase2'] = result
                    if result['exit_code'] == 0:
                        p2 = Path(read(handoff)['root'])
                        item.update(status='complete',phase2_root=str(p2),coverage=market_coverage(p2))
                        for mode,coverage in item['coverage'].items():
                            console.print(f'{day} {mode}: {coverage["available_seconds"]:,}/{coverage["seconds"]:,} seconds have available market labels')
                results.append(item)
                write(root/'summary.json',dict(status='running',results=results),immutable=False)
                if item['status'] != 'complete':
                    break
            # Confirm the ledger/manifest still identify the exact consumed build.
            if source_api.load_build(manifest,ledger,days,args.tickers) != source:
                raise ValueError('Source build certificates changed during the run')
            success = len(results) == len(days) and all(r['status'] == 'complete' for r in results)
            summary = dict(status='complete' if success else 'interrupted' if stopped(root) else 'incomplete',
                results=results,elapsed_seconds=monotonic()-started,preflight_seconds=preflight_seconds,
                total_seconds=monotonic()-invocation_started)
            write(root/'summary.json',summary,immutable=False)
            if success:
                write(root/'complete.json',dict(plan_hash=plan['plan_hash'],dates=list(map(str,days))))
            console.print(f'Result: {summary["status"]} | {len(results)}/{len(days)} sessions processed | {summary["elapsed_seconds"]:.1f}s')
            return 0 if success else 2
    finally:
        signal.signal(signal.SIGINT,previous)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, OSError) as exc:
        print('Hindsight build failed: '+str(exc),file=sys.stderr)
        raise SystemExit(2)
