"""Direct certified ARTE extraction: no teacher phases or teacher-selected population."""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
os.environ.setdefault('POLARS_MAX_THREADS','2')
import sys
sys.dont_write_bytecode = True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))

import argparse
from datetime import date
from hashlib import sha256
import numpy as np
import polars as pl
import time

from research.rl_trading.v1 import arte_source
from research.rl_trading.v1.arte_sql import ArteReader
from research.rl_trading.v1.common import bounds, digest, file_hash, exclusive
from research.rl_trading.v1.features import FEATURE_NAMES, SECONDS, encode, read_arte_seconds
from research.rl_trading.v1.reference_features import read_reference, storage_check, missing_seeds
from research.mlops.env import load_env_files
from research.mlops.clickhouse import discover_clickhouse_env_files
from research.rl_trading.v2.data import ARRAYS, DATA_VERSION, MarketSession
from research.rl_trading.v2.io import output_root, read, write, code_identity
from research.rl_trading.v2.estimated_luld import reference_series
from research.rl_trading.v2.v1_cache import catalog, discover, copy_row
from research.rl_trading.v2.build_workers import results
from research.rl_trading.v1 import arte_sql


def bank_hash(array):
    return sha256(np.ascontiguousarray(array).tobytes()).hexdigest()


def execution_arrays(bars):
    """Vectorized exact-price/activity projection, with no per-second Python loop."""
    index = bars['bucket_index'].to_numpy().astype(np.int64)-14400+1
    if np.any(index < 1) or np.any(index >= SECONDS) or np.any(np.diff(index) <= 0):
        raise ValueError('Execution bars must be unique, ordered completed session seconds')
    volume = np.zeros(SECONDS,dtype=np.float64)
    trades = np.zeros(SECONDS,dtype=np.float64)
    prices = np.zeros(SECONDS,dtype=np.float64)
    fresh = np.zeros(SECONDS,dtype=bool)
    volume[index] = bars['volume'].to_numpy()
    trades[index] = bars['trade_count'].to_numpy()
    good = bars['price_valid'].to_numpy() == 1
    fresh[index[good]] = True
    prices[index[good]] = bars['close_int'].to_numpy()[good]/10000.
    seen = np.maximum.accumulate(np.where(fresh,np.arange(SECONDS),0))
    prices = prices[seen]
    cumulative = np.concatenate(([0.],np.cumsum(trades)))
    ticks = np.arange(SECONDS)
    trades60 = cumulative[ticks+1]-cumulative[np.maximum(0,ticks-59)]
    return dict(prices=prices,volume=volume,trades_60s=trades60,fresh=fresh)


def read_execution_bars(client,source,day,ticker):
    where = arte_sql.selection(source['build_id'],day,ticker,
        source['units'][str(day)][ticker]['bars']['attempt_id'])
    return arte_source.frame(client,
        f'SELECT bucket_index,close_int,price_valid,volume,trade_count FROM arte.bars_v1 WHERE {where} '
        'AND resolution_ms=1000 ORDER BY bucket_index',
        dict(bucket_index=pl.Int64,close_int=pl.Int64,price_valid=pl.Int64,
             volume=pl.Float64,trade_count=pl.Int64))


def read_prior_close(client,source,day,ticker):
    """First pinned technical row carries the certified preceding close or zero."""
    where = arte_sql.selection(source['build_id'],day,ticker,
        source['units'][str(day)][ticker]['technical']['attempt_id'])
    rows = arte_source.frame(client,
        f'SELECT previous_close FROM arte.indicators_v1 WHERE {where} '
        'AND resolution_ms=1000 ORDER BY bucket_index LIMIT 1',
        dict(previous_close=pl.Float64))
    if len(rows) != 1:
        return np.float32(0.)
    value = float(rows['previous_close'][0])
    if not np.isfinite(value) or value < 0:
        raise ValueError(f'Invalid certified preceding close: {day} {ticker}')
    return np.float32(value)


def extract(client, source, day, listing, cached=None):
    ticker = listing['ticker']
    verify = arte_source.verify_listing if cached is None else verify_execution_source
    verify(client,source,day,ticker)
    if cached is None:
        seed,splits,fundamental,reference = read_reference(client,day,listing)
        bars,indicators = read_arte_seconds(client,source,day,ticker)
        features,volume60 = encode(day,bars,indicators,seed,splits,fundamental)
        values = dict(features=features,volume_60s=volume60)
    else:
        values,reference = copy_row(cached)
        bars = read_execution_bars(client,source,day,ticker)
    values.update(execution_arrays(bars))
    values['prior_close'] = read_prior_close(client,source,day,ticker)
    values['estimated_reference'] = reference_series(values['prices'],values['volume'],
        values['fresh'],values['prior_close'])
    verify(client,source,day,ticker)
    return values,reference


def verify_execution_source(client,source,day,ticker):
    for stage,table in (('bars','bars_v1'),('technical','indicators_v1')):
        saved = source['units'][str(day)][ticker][stage]
        where = arte_sql.selection(source['build_id'],day,ticker,saved['attempt_id'])
        actual = arte_sql.query(client,
            'SELECT count() AS n,uniqExact((resolution_ms,bucket_index)) AS unique_keys,'
            f'sum(cityHash64(tuple(*))) AS hash FROM arte.{table} WHERE {where}')[0]
        if (int(actual['n']) != saved['output_rows'] or int(actual['n']) != int(actual['unique_keys'])
                or str(actual['hash']) != saved['output_hash']):
            raise ValueError(f'Cached V1 supplemental {stage} differ from source certificate')


def worker(job):
    client = ArteReader(job['query_threads'])
    try:
        arte_source.storage_check(client)
        storage_check(client)
        if job['cached'] is None:
            return extract(client,job['source'],job['day'],job['listing'])
        return extract(client,job['source'],job['day'],job['listing'],job['cached'])
    except Exception as exc:
        raise RuntimeError(f"V2 listing {job['listing']['ticker']} failed: {exc}") from exc
    finally:
        client.close()


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest',type=Path,required=True)
    p.add_argument('--ledger',type=Path,required=True)
    p.add_argument('--date',type=date.fromisoformat,required=True)
    p.add_argument('--query-threads',type=int,default=2)
    p.add_argument('--workers',type=int,default=2,help='Bounded independent listing processes (1-16)')
    p.add_argument('--v1-shards',type=Path,nargs='+',
        help='Certified V1 banks/overlays; default discovers completed local date banks')
    args = p.parse_args(argv)
    if not 1 <= args.query_threads <= 4 or not 1 <= args.workers <= 16:
        p.error('query threads must be 1-4 and workers 1-16')
    runtime = output_root()  # Fail before opening any database if runtime unavailable.
    source = arte_source.load_build(args.manifest,args.ledger,[args.date])
    load_env_files(discover_clickhouse_env_files(),verbose=False)
    client = ArteReader(args.query_threads)
    try:
        arte_source.storage_check(client)
        storage_check(client)
        listings,population = arte_source.population(client,source,args.date)
        selected = int(population['selected_ticker_days'])
        without_events = int(population['tradable_without_canonical_events'])
        if (len(listings) != selected or selected + without_events !=
                int(population['certificate']['tradable_count'])):
            raise ValueError('V2 requires every certified event-bearing listing and explicit no-event exclusions')
        candidates = args.v1_shards if args.v1_shards is not None else [
            path.parent for path in discover(runtime.parents[1],args.date)]
        cached,cache_report = catalog(candidates,source=source,day=args.date,listings=listings)
        for item in cache_report:
            print(f'V1 cache: {item}',flush=True)
        uncached = [x['ticker'] for x in listings if str(x['listing_id']) not in cached]
        missing = missing_seeds(client,args.date,uncached) if uncached else []
        if missing:
            raise ValueError(f'Missing certified V7 for {len(missing)} listings; no silent universe exclusion: {missing[:12]}')
        plan = dict(version=DATA_VERSION,date=str(args.date),listings=listings,population=population,
            source_build_id=source['build_id'],source_definition_hash=source['definition_hash'],
            source_units=source['units'][str(args.date)],feature_names=list(FEATURE_NAMES),
            clock='completed_second',step_us=1000000,first_us=bounds(args.date)[0],rows=SECONDS,
            segment=False,teacher_dependency=False,source_manifest_hash=file_hash(args.manifest),
            band_policy='causal-prior-close-rolling-5m-v1',
            v1_market_cache=cache_report,
            code=code_identity())
        plan['plan_hash'] = digest(plan)
        root = runtime/'market'/str(args.date)/plan['plan_hash'][:20]
        root.mkdir(parents=True,exist_ok=True)
        with exclusive(root/'build.lock'):
            if (root/'complete.json').exists():
                MarketSession.load(root)
                print(f'Reused verified market session: {root}',flush=True)
                return 0
            write(root/'plan.json',plan)
            arrays = {}
            for name in ARRAYS:
                shape = ((len(listings),SECONDS,len(FEATURE_NAMES)) if name == 'features'
                         else (len(listings),) if name == 'prior_close'
                         else (len(listings),SECONDS))
                dtype = (np.float32 if name in ('features','estimated_reference','prior_close')
                         else np.bool_ if name == 'fresh' else np.float64)
                path = root/(name+'.npy')
                arrays[name] = np.load(path,mmap_mode='r+') if path.exists() else np.lib.format.open_memmap(path,mode='w+',dtype=dtype,shape=shape)
                if arrays[name].shape != shape or arrays[name].dtype != dtype:
                    raise ValueError('Restart bank differs from planned shape/dtype')
            counts = dict(completed=0,reused=0,failed=0,retried=0,skipped=0,
                          copied_v1=0,extracted=0)
            jobs = []
            for index,listing in enumerate(listings):
                if (root/'STOP').exists():
                    write(root/'progress.json',dict(**counts,active=0,queued=len(listings)-counts['reused'],status='stopped'))
                    return 2
                ready = root/'progress'/f'{index}.json'
                if ready.exists():
                    saved = read(ready)
                    if saved['listing'] != listing or any(bank_hash(arrays[name][index]) != saved['hashes'][name] for name in ARRAYS):
                        raise ValueError('Restart listing integrity changed')
                    counts['reused'] += 1
                else:
                    unit_source = dict(build_id=source['build_id'],units={str(args.date):{
                        listing['ticker']:source['units'][str(args.date)][listing['ticker']]}})
                    jobs.append(dict(index=index,listing=listing,source=unit_source,day=args.date,
                        query_threads=args.query_threads,cached=cached.get(str(listing['listing_id']))))
            started = time.monotonic()
            def progress(state):
                remaining = max(0,len(jobs)-counts['completed']-counts['failed'])
                active = min(args.workers,remaining) if state == 'running' else 0
                write(root/'progress.json',dict(**counts,status=state,active=active,
                    queued=remaining-active,workers=args.workers,elapsed_seconds=time.monotonic()-started))
            progress('running')
            try:
                for job,(values,reference) in results(jobs,worker,workers=args.workers,
                        stopped=lambda:(root/'STOP').exists()):
                    index,listing = job['index'],job['listing']
                    for name,value in values.items():
                        arrays[name][index] = value
                        arrays[name].flush()
                    write(root/'progress'/f'{index}.json',dict(listing=listing,reference=reference,
                        v1_cache=job['cached'],hashes={name:bank_hash(arrays[name][index]) for name in ARRAYS}))
                    counts['completed'] += 1
                    counts['copied_v1' if job['cached'] else 'extracted'] += 1
                    progress('running')
                    print(f'{args.date} {counts} remaining={len(jobs)-counts["completed"]}',flush=True)
            except BaseException:
                counts['failed'] += 1
                progress('failed')
                raise
            if (root/'STOP').exists():
                progress('stopped')
                return 2
            MarketSession(plan,arrays,root)
            write(root/'complete.json',dict(plan_hash=plan['plan_hash'],listing_count=len(listings),
                files={name+'.npy':file_hash(root/(name+'.npy')) for name in ARRAYS}))
            write(root/'progress.json',dict(**counts,active=0,queued=0,status='complete'))
            print(f'Published market session: {root}',flush=True)
        return 0
    finally:
        client.close()


if __name__ == '__main__':
    raise SystemExit(main())
