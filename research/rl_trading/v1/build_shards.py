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
from dataclasses import asdict
from datetime import date
from hashlib import sha256
import json

import numpy as np
import polars as pl
from rich.console import Console

from research.mlops.env import load_env_files
from research.mlops.clickhouse import discover_clickhouse_env_files
from research.rl_trading.v1 import arte_source
from research.rl_trading.v1.arte_sql import ArteReader, POLICY, query, literal
from research.rl_trading.v1.common import bounds, digest, file_hash
from research.rl_trading.v1.features import FEATURE_NAMES, SECONDS, encode, read_arte_seconds
from research.rl_trading.v1.phase3_search import VERSION as PHASE3_VERSION
from research.rl_trading.v1.shard_labels import pack
from src.backend.backtest_market_data import CertifiedMarketDayPlan, ExecutionInterval, MarketDayUnit
from src.backend.causal_v7_reader import CausalV7Cursor, certified_plan
from src.market_engine.causal_v7_contract import COVERAGE_TABLE, LEVEL_TABLE, STATE_TABLE
from src.market_engine.level_book_store import read, write
from src.runtime_paths import runtime_root

VERSION = 'rl-trading-causal-shards-v1'
PRODUCTS = tuple(x.split('.')[1] for x in (STATE_TABLE,LEVEL_TABLE,COVERAGE_TABLE))
SOURCES = ('build_shards.py','features.py','shard_labels.py','universe.py',
    'phase3_search.py','arte_source.py','arte_sql.py')


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


def _v7_plan(client, day: date, p1: dict):
    names = ','.join(literal(name) for name in PRODUCTS)
    tables = query(client,f"SELECT name,storage_policy FROM system.tables WHERE database='arte' AND name IN ({names})")
    if {row['name'] for row in tables} != set(PRODUCTS) or any(row['storage_policy'] != POLICY for row in tables):
        raise ValueError('Pinned causal V7 arte tables are absent or misplaced')
    parts = query(client,f"SELECT table,disk_name FROM system.parts WHERE database='arte' AND active AND table IN ({names}) GROUP BY table,disk_name")
    if any(row['disk_name'] != POLICY for row in parts):
        raise ValueError('Causal V7 active parts are not on live_market_ssd')
    units = tuple(MarketDayUnit(p1['source_build_id'],str(day),ticker,'bars',
        p1['source_units'][ticker]['bars']['attempt_id'],
        p1['source_units'][ticker]['bars']['source_hash'],
        p1['source_units'][ticker]['bars']['output_rows'],
        p1['source_units'][ticker]['bars']['output_hash'])
        for ticker in sorted(p1['source_units']))
    market = CertifiedMarketDayPlan(ExecutionInterval.parse('1s'),
        p1['source_build_id'],p1['source_definition_hash'],(str(day),),
        tuple(item.ticker for item in units),units,(1000,),digest([asdict(x) for x in units]))
    return certified_plan(market,None,client)


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
        v7 = _v7_plan(client,day,p1)
        v7_units = {item.ticker:item for item in v7.units}
        if set(v7_units) != set(tickers):
            raise ValueError('Causal V7 coverage differs from teacher population')
        runtime = runtime_root().resolve()
        if not runtime.is_dir():
            raise ValueError('Required runtime root is unavailable')
        plan = dict(version=VERSION,date=str(day),phase3_root=str(phase3),
            phase3_plan_hash=teacher['plan_hash'],phase3_complete_hash=file_hash(phase3/'complete.json'),
            phase2_root=str(phase2),phase2_plan_hash=p2['plan_hash'],
            phase1_plan_hash=p1['plan_hash'],market_build_id=p1['source_build_id'],
            v7_token=v7.token,v7_catalog_hash=v7.catalog_hash,
            tickers=tickers,top_n=int(teacher['config']['top_n']),
            history_seconds=args.history_seconds,feature_names=FEATURE_NAMES,
            feature_dtype='float32',volume_dtype='float64',
            teacher_first_us=teacher['first_us'],teacher_end_us=teacher['end_us'],
            segment=teacher['end_us'] != teacher['true_session_cutoff_us'],
            max_lots=teacher['config']['max_lots'],max_orders=teacher['config']['max_orders_per_second'],
            allocation_step=teacher['config']['allocation_step'],initial_cash=teacher['config']['initial_cash'],
            liquidity_filter=p2['liquidity_filter'],
            code_hashes={name:file_hash(REPO/'research/rl_trading/v1'/name) for name in SOURCES})
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
        progress_path = root/'progress.json'
        progress = read(progress_path) if progress_path.exists() else dict(plan_hash=plan['plan_hash'],done={})
        if progress['plan_hash'] != plan['plan_hash']:
            raise ValueError('Shard restart provenance changed')
        source = dict(build_id=p1['source_build_id'],units={str(day):p1['source_units']})
        for index,listing in enumerate(p2['selected']):
            ticker = listing['ticker']
            if (root/'STOP').exists():
                console.print(f'Stopped after {index} listings; rerun to resume')
                return 2
            saved = progress['done'].get(ticker)
            if saved:
                if (saved['features'] != _hash_bytes(bank[index]) or
                        saved['volume'] != _hash_bytes(volumes[index])):
                    raise ValueError('Restart ticker slice changed: '+ticker)
                continue
            arte_source.verify_listing(client,source,day,ticker)
            cursor = CausalV7Cursor(v7_units[ticker],client)
            bars,indicators = read_arte_seconds(client,source,day,ticker)
            features,volume60 = encode(day,bars,indicators,cursor)
            folder = phase2/'listings'/digest(listing)[:20]
            ready = read(folder/'ready.json')
            _verified(folder/'coefficients.parquet',ready['files']['coefficients.parquet'])
            columns = pl.read_parquet(folder/'coefficients.parquet',columns=['side','volume_60s'])
            expected = columns.filter(pl.col('side') == 'long')['volume_60s'].to_numpy()
            if len(expected) != SECONDS or not np.allclose(volume60,expected,rtol=1e-9,atol=1e-4):
                raise ValueError('Arte completed-minute volume differs from Phase 2: '+ticker)
            bank[index] = features
            volumes[index] = volume60
            bank.flush();volumes.flush()
            progress['done'][ticker] = dict(features=_hash_bytes(bank[index]),
                volume=_hash_bytes(volumes[index]),v7_attempt=v7_units[ticker].attempt_id)
            write(progress_path,progress,immutable=False)
            if (index+1)%10 == 0 or index+1 == len(tickers):
                console.print(f'Features {index+1:,}/{len(tickers):,} listings | queued {len(tickers)-index-1:,} | failed 0')
        trajectory = pl.read_parquet(phase3/'trajectory.parquet').to_dicts()
        packed = pack(trajectory,bank,volumes,tickers,left_us=left,
            top_n=plan['top_n'],max_lots=plan['max_lots'],max_orders=plan['max_orders'],
            allocation_step=plan['allocation_step'],initial_cash=plan['initial_cash'],
            min_volume=float(p2['liquidity_filter']['min_volume_60s']),
            min_trades=int(p2['liquidity_filter']['min_trades_60s']))
        hashes = {name:file_hash(root/name) for name in ('features.npy','volume_60s.npy')}
        for name,value in packed.items():
            hashes[name+'.npy'] = _save_array(root/(name+'.npy'),value)
        if (file_hash(phase3/'complete.json') != plan['phase3_complete_hash'] or
                any(progress['done'][ticker]['features'] != _hash_bytes(bank[i])
                    for i,ticker in enumerate(tickers))):
            raise ValueError('Training shard source or features changed during publication')
        write(complete_path,dict(plan_hash=plan['plan_hash'],rows=len(trajectory),
            listings=len(tickers),files=hashes,teacher_optimality=teacher['optimality'],
            v7_token=v7.token))
        console.print(f'Complete | {len(trajectory):,} seconds | {len(tickers):,} tickers | {root}',soft_wrap=True)
        return 0
    finally:
        client.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase3',type=Path,required=True)
    parser.add_argument('--history-seconds',type=int,default=120)
    parser.add_argument('--query-threads',type=int,choices=(1,2,3,4),default=2)
    parser.add_argument('--allow-segment',action='store_true',help='Only for bounded validation; segment remains marked')
    args = parser.parse_args(argv)
    if args.history_seconds < 1 or args.history_seconds > SECONDS:
        parser.error('history-seconds must be within the session')
    return run(args,Console())


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, OSError, RuntimeError) as exc:
        print('RL shard build failed: '+str(exc),file=sys.stderr)
        raise SystemExit(2)
