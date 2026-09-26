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

from research.rl_trading.v1 import arte_source
from research.rl_trading.v1.arte_sql import ArteReader
from research.rl_trading.v1.common import bounds, digest, file_hash, exclusive
from research.rl_trading.v1.features import FEATURE_NAMES, SECONDS, encode, read_arte_seconds
from research.rl_trading.v1.reference_features import read_reference, storage_check, missing_seeds
from research.mlops.env import load_env_files
from research.mlops.clickhouse import discover_clickhouse_env_files
from research.rl_trading.v2.data import ARRAYS, DATA_VERSION, MarketSession
from research.rl_trading.v2.io import output_root, read, write, code_identity


def bank_hash(array):
    return sha256(np.ascontiguousarray(array).tobytes()).hexdigest()


def extract(client, source, day, listing):
    ticker = listing['ticker']
    arte_source.verify_listing(client,source,day,ticker)
    seed,splits,fundamental,reference = read_reference(client,day,listing)
    bars,indicators = read_arte_seconds(client,source,day,ticker)
    features,volume60 = encode(day,bars,indicators,seed,splits,fundamental)
    index = bars['bucket_index'].to_numpy().astype(np.int64)-14400+1
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
    arte_source.verify_listing(client,source,day,ticker)
    return dict(features=features,prices=prices,volume=volume,volume_60s=volume60,
                trades_60s=trades60,fresh=fresh),reference


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest',type=Path,required=True)
    p.add_argument('--ledger',type=Path,required=True)
    p.add_argument('--date',type=date.fromisoformat,required=True)
    p.add_argument('--query-threads',type=int,default=2)
    args = p.parse_args(argv)
    if args.query_threads < 1:
        p.error('query threads must be positive')
    runtime = output_root()  # Fail before opening any database if runtime unavailable.
    source = arte_source.load_build(args.manifest,args.ledger,[args.date])
    load_env_files(discover_clickhouse_env_files(),verbose=False)
    client = ArteReader(args.query_threads)
    try:
        arte_source.storage_check(client)
        storage_check(client)
        listings,population = arte_source.population(client,source,args.date)
        if len(listings) != int(population['certificate']['tradable_count']):
            raise ValueError('V2 requires the entire certified tradable population, not a build subset')
        missing = missing_seeds(client,args.date,[x['ticker'] for x in listings])
        if missing:
            raise ValueError(f'Missing certified V7 for {len(missing)} listings; no silent universe exclusion: {missing[:12]}')
        plan = dict(version=DATA_VERSION,date=str(args.date),listings=listings,population=population,
            source_build_id=source['build_id'],source_definition_hash=source['definition_hash'],
            source_units=source['units'][str(args.date)],feature_names=list(FEATURE_NAMES),
            clock='completed_second',step_us=1000000,first_us=bounds(args.date)[0],rows=SECONDS,
            segment=False,teacher_dependency=False,source_manifest_hash=file_hash(args.manifest),
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
                shape = (len(listings),SECONDS,len(FEATURE_NAMES)) if name == 'features' else (len(listings),SECONDS)
                dtype = np.float32 if name == 'features' else np.bool_ if name == 'fresh' else np.float64
                path = root/(name+'.npy')
                arrays[name] = np.load(path,mmap_mode='r+') if path.exists() else np.lib.format.open_memmap(path,mode='w+',dtype=dtype,shape=shape)
                if arrays[name].shape != shape or arrays[name].dtype != dtype:
                    raise ValueError('Restart bank differs from planned shape/dtype')
            counts = dict(completed=0,reused=0,failed=0,retried=0,skipped=0)
            for index,listing in enumerate(listings):
                if (root/'STOP').exists():
                    write(root/'progress.json',dict(**counts,active=0,queued=len(listings)-index,status='stopped'))
                    return 2
                ready = root/'progress'/f'{index}.json'
                if ready.exists():
                    saved = read(ready)
                    if saved['listing'] != listing or any(bank_hash(arrays[name][index]) != saved['hashes'][name] for name in ARRAYS):
                        raise ValueError('Restart listing integrity changed')
                    counts['reused'] += 1
                else:
                    write(root/'progress.json',dict(**counts,active=1,queued=len(listings)-index-1,ticker=listing['ticker']))
                    try:
                        values,reference = extract(client,source,args.date,listing)
                        for name,value in values.items():
                            arrays[name][index] = value
                            arrays[name].flush()
                        write(ready,dict(listing=listing,reference=reference,
                            hashes={name:bank_hash(arrays[name][index]) for name in ARRAYS}))
                        counts['completed'] += 1
                    except Exception:
                        counts['failed'] += 1
                        write(root/'progress.json',dict(**counts,active=0,queued=len(listings)-index-1,status='failed',ticker=listing['ticker']))
                        raise
                print(f"{args.date} completed={counts['completed']} reused={counts['reused']} queued={len(listings)-index-1} failed={counts['failed']}",flush=True)
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
