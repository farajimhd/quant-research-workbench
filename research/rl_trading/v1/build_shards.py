"""Extract certified arte market features and Phase 3 teacher into disk shards.

The producer is SELECT-only. A session shard stores each ticker's one-second
features once; training gathers rolling T-second windows on the GPU.
"""
from __future__ import annotations

import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import sys
sys.dont_write_bytecode = True
from pathlib import Path
REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0,str(REPO))

import argparse
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from datetime import date
from hashlib import sha256
import json

import numpy as np
import polars as pl
from rich.console import Console

from research.mlops.env import load_env_files
from research.mlops.clickhouse import discover_clickhouse_env_files
from research.rl_trading.v1 import arte_source
from research.rl_trading.v1.arte_sql import ArteReader
from research.rl_trading.v1.common import bounds, digest, file_hash
from research.rl_trading.v1.features import FEATURE_NAMES, SECONDS, encode, read_arte_seconds
from research.rl_trading.v1.reference_features import (missing_seeds, read_reference,
    storage_check, VERSION as REFERENCE_VERSION)
from research.rl_trading.v1.phase3_search import VERSION as PHASE3_VERSION
from research.rl_trading.v1.shard_labels import pack
from src.market_engine.level_book_store import read, write
from src.runtime_paths import runtime_root

VERSION = 'rl-trading-structural-shards-v3'
SOURCES = ('build_shards.py','features.py','shard_labels.py','universe.py',
    'phase3_search.py','arte_source.py','arte_sql.py','reference_features.py')
ENGINE_SOURCES = ('src/backend/fixed_v7_stream.py','src/backend/structural_v7_seed.py',
    'src/market_engine/streaming_level_book.py','src/market_engine/v7_qmd.py')
_WORKER_CLIENT = None
_WORKER_SOURCE = None
_WORKER_DAY = None


def _extract(client, source, day, listing):
    ticker = listing['ticker']
    arte_source.verify_listing(client,source,day,ticker)
    seed,splits,fundamental,reference = read_reference(client,day,listing)
    bars,indicators = read_arte_seconds(client,source,day,ticker)
    features,volume60 = encode(day,bars,indicators,seed,splits,fundamental)
    return features,volume60,reference


def _worker_init(query_threads, source, day):
    global _WORKER_CLIENT, _WORKER_SOURCE, _WORKER_DAY
    _WORKER_CLIENT = ArteReader(query_threads)
    _WORKER_SOURCE = source
    _WORKER_DAY = day


def _worker_extract(listing):
    return _extract(_WORKER_CLIENT,_WORKER_SOURCE,_WORKER_DAY,listing)


def _extractions(items, *, workers, query_threads, source, day, client):
    """Yield completed independent listings with bounded in-flight work."""
    if workers == 1:
        for index,listing in items:
            yield index,listing,_extract(client,source,day,listing)
        return
    iterator = iter(items)
    with ProcessPoolExecutor(max_workers=workers,initializer=_worker_init,
            initargs=(query_threads,source,day)) as pool:
        pending = {}
        def submit():
            item = next(iterator,None)
            if item is not None:
                index,listing = item
                pending[pool.submit(_worker_extract,listing)] = (index,listing)
        for _ in range(workers*2):
            submit()
        while pending:
            done,_ = wait(pending,return_when=FIRST_COMPLETED)
            for future in done:
                index,listing = pending.pop(future)
                yield index,listing,future.result()
                submit()


def _hash_bytes(array) -> str:
    return sha256(np.ascontiguousarray(array).tobytes()).hexdigest()


def _verified(path: Path, expected: str) -> None:
    if not path.is_file() or file_hash(path) != expected:
        raise ValueError('Source artifact integrity failure: ' + str(path))


def _source(phase3: Path):
    teacher = read(phase3/'plan.json')
    complete = read(phase3/'complete.json')
    if (teacher.get('version') != PHASE3_VERSION or not teacher['config'].get('top_n')
            or teacher.get('plan_hash') != digest({k:v for k,v in teacher.items() if k != 'plan_hash'})
            or complete.get('plan_hash') != teacher['plan_hash']):
        raise ValueError('Require complete Phase 3 V2 with causal top-N membership')
    for name,certificate in complete['files'].items():
        _verified(phase3/name,certificate['file_hash'])
    phase2 = Path(teacher['phase2_root']).resolve()
    p2 = read(phase2/'plan.json')
    p2complete = read(phase2/'complete.json')
    if (p2.get('plan_hash') != teacher['phase2_plan_hash'] or
            p2['plan_hash'] != digest({k:v for k,v in p2.items() if k != 'plan_hash'}) or
            p2complete.get('plan_hash') != p2['plan_hash'] or
            p2.get('valuation_basis') != 'price_action'):
        raise ValueError('Teacher Phase 2 provenance changed')
    _verified(phase2/'plan.json',teacher['phase2_plan_file_hash'])
    _verified(phase2/'complete.json',teacher['phase2_complete_file_hash'])
    for name,expected in teacher['phase2_tensor_file_hashes'].items():
        _verified(phase2/name,expected)
    phase1 = Path(p2['phase1_root']).resolve()
    p1 = read(phase1/'plan.json')
    p1complete = read(phase1/'complete.json')
    if (p1.get('plan_hash') != p2['phase1_plan_hash'] or
            p1['plan_hash'] != digest({k:v for k,v in p1.items() if k != 'plan_hash'}) or
            p1complete.get('plan_hash') != p1['plan_hash'] or
            p1.get('selected') != p2.get('selected') or p1.get('date') != p2.get('date')):
        raise ValueError('Teacher Phase 1 provenance changed')
    return teacher,complete,phase2,p2,phase1,p1


def _open_bank(path: Path, shape, dtype):
    if path.exists():
        value = np.load(path,mmap_mode='r+')
        if value.shape != shape or value.dtype != np.dtype(dtype):
            raise ValueError('Restart bank shape or dtype mismatch: '+str(path))
        return value
    return np.lib.format.open_memmap(path,mode='w+',dtype=dtype,shape=shape)


def _save_array(path: Path, value: np.ndarray) -> str:
    temporary = path.with_suffix(path.suffix+'.tmp')
    with temporary.open('wb') as stream:
        np.save(stream,value,allow_pickle=False)
    if path.exists():
        if file_hash(path) != file_hash(temporary):
            raise ValueError('Immutable shard array changed: '+str(path))
        temporary.unlink()
    else:
        temporary.replace(path)
    return file_hash(path)


def run(args,console):
    phase3 = args.phase3.resolve()
    teacher,teacher_complete,phase2,p2,phase1,p1 = _source(phase3)
    day = date.fromisoformat(p2['date'])
    left,_ = bounds(day)
    if not args.allow_segment and (teacher['first_us'] != left or
            teacher['end_us'] != teacher['true_session_cutoff_us']):
        raise ValueError('Training shards require a complete 04:00–19:58 teacher session')
    tickers = [item['ticker'] for item in p2['selected']]
    if len(tickers) != len(set(tickers)) or set(tickers) != set(p1['source_units']):
        raise ValueError('Teacher ticker population differs from pinned arte units')
    load_env_files(discover_clickhouse_env_files(),verbose=False)
    client = ArteReader(args.query_threads)
    try:
        arte_source.storage_check(client)
        storage_check(client)
        runtime = runtime_root().resolve()
        if not runtime.is_dir():
            raise ValueError('Required runtime root is unavailable')
        missing = missing_seeds(client,day,tickers)
        if missing:
            raise ValueError(f'{len(missing)} pinned listings lack prior arte structural V7 coverage: '
                + ', '.join(missing[:12]))
        plan = dict(version=VERSION,date=str(day),phase3_root=str(phase3),
            phase3_plan_hash=teacher['plan_hash'],phase3_complete_hash=file_hash(phase3/'complete.json'),
            phase2_root=str(phase2),phase2_plan_hash=p2['plan_hash'],
            phase1_plan_hash=p1['plan_hash'],market_build_id=p1['source_build_id'],
            reference_contract=REFERENCE_VERSION,
            tickers=tickers,top_n=int(teacher['config']['top_n']),
            history_seconds=args.history_seconds,feature_names=FEATURE_NAMES,
            feature_dtype='float32',volume_dtype='float64',
            teacher_first_us=teacher['first_us'],teacher_end_us=teacher['end_us'],
            segment=teacher['end_us'] != teacher['true_session_cutoff_us'],
            max_lots=teacher['config']['max_lots'],max_orders=teacher['config']['max_orders_per_second'],
            allocation_step=teacher['config']['allocation_step'],initial_cash=teacher['config']['initial_cash'],
            liquidity_filter=p2['liquidity_filter'],
            code_hashes={**{name:file_hash(REPO/'research/rl_trading/v1'/name) for name in SOURCES},
                         **{name:file_hash(REPO/name) for name in ENGINE_SOURCES}})
        plan['plan_hash'] = digest(plan)
        root = runtime/'rl-trading-shards'/str(day)/plan['plan_hash'][:20]
        root.mkdir(parents=True,exist_ok=True)
        write(root/'plan.json',plan)
        complete_path = root/'complete.json'
        if complete_path.exists():
            complete = read(complete_path)
            if complete['plan_hash'] != plan['plan_hash'] or any(
                    file_hash(root/name) != value for name,value in complete['files'].items()):
                raise ValueError('Completed training shard integrity changed')
            console.print('Reused verified training shard: '+str(root))
            return 0
        bank = _open_bank(root/'features.npy',(len(tickers),SECONDS,len(FEATURE_NAMES)),np.float32)
        volumes = _open_bank(root/'volume_60s.npy',(len(tickers),SECONDS),np.float64)
        execution = _open_bank(root/'execution.npy',(len(tickers),SECONDS,3),np.float64)
        closeable = _open_bank(root/'closeable.npy',(len(tickers),SECONDS),np.bool_)
        progress_path = root/'progress.json'
        progress = read(progress_path) if progress_path.exists() else dict(plan_hash=plan['plan_hash'],done={})
        if progress['plan_hash'] != plan['plan_hash']:
            raise ValueError('Shard restart provenance changed')
        source = dict(build_id=p1['source_build_id'],units={str(day):p1['source_units']})
        pending = []
        for index,listing in enumerate(p2['selected']):
            ticker = listing['ticker']
            if (root/'STOP').exists():
                console.print(f'Stopped after {index} listings; rerun to resume')
                return 2
            saved = progress['done'].get(ticker)
            if saved:
                if (saved['features'] != _hash_bytes(bank[index]) or
                        saved['volume'] != _hash_bytes(volumes[index]) or
                        saved['execution'] != _hash_bytes(execution[index]) or
                        saved['closeable'] != _hash_bytes(closeable[index])):
                    raise ValueError('Restart ticker slice changed: '+ticker)
                continue
            pending.append((index,listing))
        completed_batch = []
        for index,listing,(features,volume60,reference) in _extractions(pending,
                workers=args.workers,query_threads=args.query_threads,source=source,
                day=day,client=client):
            ticker = listing['ticker']
            if (root/'STOP').exists():
                console.print(f'Stopped after {len(progress["done"])} listings; rerun to resume')
                return 2
            folder = phase2/'listings'/digest(listing)[:20]
            ready = read(folder/'ready.json')
            _verified(folder/'coefficients.parquet',ready['files']['coefficients.parquet'])
            columns = pl.read_parquet(folder/'coefficients.parquet',columns=[
                'side','time_us','volume_60s','can_close','entry_price','close_price',
                'capital_per_share'])
            long = columns.filter(pl.col('side') == 'long').sort('time_us')
            expected = long['volume_60s'].to_numpy()
            teacher_seconds = (teacher['end_us']-left)//1_000_000+1
            if (len(expected) != SECONDS or teacher_seconds > SECONDS or
                    not np.allclose(volume60[:teacher_seconds],expected[:teacher_seconds],rtol=1e-9,atol=1e-4)):
                raise ValueError('Arte completed-minute volume differs from Phase 2: '+ticker)
            times = left+np.arange(SECONDS,dtype=np.int64)*1_000_000
            if not np.array_equal(long['time_us'].to_numpy(),times):
                raise ValueError('Phase 2 execution grid differs from training seconds: '+ticker)
            price_columns = ('entry_price','close_price','capital_per_share')
            prices = long.select(price_columns).fill_null(0.).to_numpy().astype(np.float64)
            available = long['can_close'].to_numpy().astype(np.bool_)
            if (np.any(~np.isfinite(prices)) or np.any(prices < 0) or
                    np.any(available & (prices[:,1] <= 0))):
                raise ValueError('Phase 2 execution prices are malformed: '+ticker)
            bank[index] = features
            volumes[index] = volume60
            execution[index] = prices
            closeable[index] = available
            completed_batch.append((index,ticker,reference))
            if len(completed_batch) == 10 or len(progress['done'])+len(completed_batch) == len(tickers):
                bank.flush();volumes.flush();execution.flush();closeable.flush()
                for done_index,done_ticker,done_reference in completed_batch:
                    progress['done'][done_ticker] = dict(features=_hash_bytes(bank[done_index]),
                        volume=_hash_bytes(volumes[done_index]),execution=_hash_bytes(execution[done_index]),
                        closeable=_hash_bytes(closeable[done_index]),reference=done_reference)
                completed_batch.clear()
                write(progress_path,progress,immutable=False)
                console.print(f'Features {len(progress["done"]):,}/{len(tickers):,} listings | '
                    f'queued {len(tickers)-len(progress["done"]):,} | failed 0')
        trajectory = pl.read_parquet(phase3/'trajectory.parquet').to_dicts()
        packed = pack(trajectory,bank,volumes,tickers,left_us=left,
            top_n=plan['top_n'],max_lots=plan['max_lots'],max_orders=plan['max_orders'],
            allocation_step=plan['allocation_step'],initial_cash=plan['initial_cash'],
            min_volume=float(p2['liquidity_filter']['min_volume_60s']),
            min_trades=int(p2['liquidity_filter']['min_trades_60s']))
        hashes = {name:file_hash(root/name) for name in (
            'features.npy','volume_60s.npy','execution.npy','closeable.npy')}
        for name,value in packed.items():
            hashes[name+'.npy'] = _save_array(root/(name+'.npy'),value)
        if (file_hash(phase3/'complete.json') != plan['phase3_complete_hash'] or
                any(progress['done'][ticker]['features'] != _hash_bytes(bank[i])
                    for i,ticker in enumerate(tickers))):
            raise ValueError('Training shard source or features changed during publication')
        write(complete_path,dict(plan_hash=plan['plan_hash'],rows=len(trajectory),
            listings=len(tickers),files=hashes,teacher_optimality=teacher['optimality'],
            teacher_profit=float(teacher_complete['terminal_profit']),
            reference_contract=REFERENCE_VERSION))
        console.print(f'Complete | {len(trajectory):,} seconds | {len(tickers):,} tickers | {root}',soft_wrap=True)
        return 0
    finally:
        client.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase3',type=Path,required=True)
    parser.add_argument('--history-seconds',type=int,default=120)
    parser.add_argument('--query-threads',type=int,choices=(1,2,3,4),default=2)
    parser.add_argument('--workers',type=int,default=8,
        help='Bounded independent arte/V7 ticker encoders')
    parser.add_argument('--allow-segment',action='store_true',help='Only for bounded validation; segment remains marked')
    args = parser.parse_args(argv)
    if args.history_seconds < 1 or args.history_seconds > SECONDS or not 1 <= args.workers <= 32:
        parser.error('history-seconds or workers outside supported bounds')
    return run(args,Console())


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, OSError, RuntimeError) as exc:
        print('RL shard build failed: '+str(exc),file=sys.stderr)
        raise SystemExit(2)
