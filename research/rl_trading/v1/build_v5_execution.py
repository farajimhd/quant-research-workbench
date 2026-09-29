"""Restart-safe, read-only ARTE projection for V5 model replay.

Only actual certified one-second bar opens and prior closes populate this
execution-only grid. The model sees its separate causal feature bank, never
the next-second opening price or volume before its decision.
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import sys
sys.dont_write_bytecode = True
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import argparse
from datetime import date
from hashlib import sha256

import numpy as np

from research.mlops.env import load_env_files
from research.mlops.clickhouse import discover_clickhouse_env_files
from research.rl_trading.v1 import arte_source
from research.rl_trading.v1.arte_sql import ArteReader
from research.rl_trading.v1.common import digest, exclusive, file_hash
from research.rl_trading.v1.features import SECONDS
from research.rl_trading.v1.v5_execution_binding import (
    VERSION, project_certified_seconds)
from research.rl_trading.v1.v5_feature_binding import bind_existing_features
from research.rl_trading.v2.build_data import (read_execution_bars,
    read_prior_close, verify_execution_source)
from src.market_engine.level_book_store import read, write
from src.runtime_paths import runtime_root

ARRAYS = dict(close=np.float32, next_open=np.float32, volume=np.float32,
              fresh=np.bool_, estimated_reference=np.float32,
              prior_close=np.float32)


def _row_hash(row):
    return sha256(np.ascontiguousarray(row).tobytes()).hexdigest()


def _open_array(root, name, shape, dtype):
    path = root / f'{name}.npy'
    if path.exists():
        result = np.load(path, mmap_mode='r+', allow_pickle=False)
        if result.shape != shape or result.dtype != np.dtype(dtype):
            raise ValueError(f'Partial V5 execution grid changed shape: {path}')
        return result
    return np.lib.format.open_memmap(path, mode='w+', dtype=dtype, shape=shape)


def _plan(binding, shard_root, source, manifest, ledger):
    shard = read(shard_root / 'plan.json')
    if (shard.get('market_build_id') != source['build_id'] or
            shard.get('date') != binding.date or
            tuple(shard.get('tickers', ())) != binding.tickers):
        raise ValueError('ARTE execution source differs from certified feature bank')
    plan = dict(version=VERSION, date=binding.date,
        tickers=list(binding.tickers), seconds=SECONDS,
        feature_bank_hash=binding.features_hash,
        supervision_plan_hash=binding.supervision_plan_hash,
        market_build_id=source['build_id'],
        market_definition_hash=source['definition_hash'],
        market_manifest=str(manifest), market_ledger=str(ledger),
        source_attempts={ticker:source['units'][binding.date][ticker]
                         for ticker in binding.tickers},
        account_clock='completed_second_decision_then_next_second_open_IOC_proxy',
        execution_source='certified_arte.bars_v1_open_int_and_volume',
        prior_close_source='certified_arte.indicators_v1_previous_close',
        estimated_luld='causal_v2_research_proxy_not_official',
        code_hashes={name:file_hash(Path(__file__).resolve().parents[3]/name)
            for name in ('research/rl_trading/v1/build_v5_execution.py',
                         'research/rl_trading/v1/v5_execution_binding.py',
                         'research/rl_trading/v2/build_data.py',
                         'research/rl_trading/v2/estimated_luld.py')})
    plan['plan_hash'] = digest(plan)
    return plan


def build(*, supervision_root, shard_root, market_manifest, market_ledger):
    runtime = runtime_root().resolve()
    paths = [Path(value).resolve() for value in
             (supervision_root, shard_root, market_manifest, market_ledger)]
    if not runtime.is_dir() or any(not p.is_relative_to(runtime) for p in paths):
        raise ValueError('All certified inputs and output must stay under runtime root')
    supervision_root, shard_root, market_manifest, market_ledger = paths
    binding = bind_existing_features(supervision_root, shard_root, runtime)
    source = arte_source.load_build(market_manifest, market_ledger,
                                   [binding.date], tickers=list(binding.tickers))
    plan = _plan(binding, shard_root, source, market_manifest, market_ledger)
    root = runtime / 'rl-trading-v5-execution' / binding.date / plan['plan_hash'][:20]
    root.mkdir(parents=True, exist_ok=True)
    write(root / 'plan.json', plan)
    with exclusive(root / 'run.lock'):
        complete_path = root / 'complete.json'
        if complete_path.exists():
            complete = read(complete_path)
            if (complete.get('plan_hash') != plan['plan_hash'] or
                    any(file_hash(root / name) != expected
                        for name, expected in complete['files'].items())):
                raise ValueError('Completed V5 execution grid failed byte validation')
            return root
        n = len(binding.tickers)
        arrays = {name:_open_array(root, name,
            (n,) if name == 'prior_close' else (n, SECONDS), dtype)
            for name, dtype in ARRAYS.items()}
        progress = root / 'progress'
        progress.mkdir(exist_ok=True)
        load_env_files(discover_clickhouse_env_files(), verbose=False)
        client = ArteReader(threads=2)
        try:
            arte_source.storage_check(client)
            for index, ticker in enumerate(binding.tickers):
                status = progress / f'{index:05d}.json'
                if status.exists():
                    saved = read(status)
                    if (saved.get('ticker') != ticker or
                            saved.get('hashes') != {name:_row_hash(array[index])
                                for name, array in arrays.items()}):
                        raise ValueError(f'Certified V5 execution row changed: {ticker}')
                    continue
                verify_execution_source(client, source, binding.date, ticker)
                bars = read_execution_bars(client, source, binding.date, ticker)
                prior = read_prior_close(client, source, binding.date, ticker)
                projected = project_certified_seconds(bars, prior)
                for name, array in arrays.items():
                    array[index] = projected[name]
                    array.flush()
                write(status, dict(ticker=ticker,
                    hashes={name:_row_hash(array[index])
                            for name, array in arrays.items()}))
                if (index+1) % 100 == 0:
                    print(f'{binding.date}: {index+1}/{n} execution listings', flush=True)
            complete = dict(plan_hash=plan['plan_hash'], listings=n,
                files={f'{name}.npy':file_hash(root / f'{name}.npy')
                       for name in ARRAYS})
            write(complete_path, complete)
        finally:
            client.close()
            arrays.clear()
    return root


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--supervision', type=Path, required=True)
    parser.add_argument('--features', type=Path, required=True)
    parser.add_argument('--market-manifest', type=Path, required=True)
    parser.add_argument('--market-ledger', type=Path, required=True)
    args = parser.parse_args(argv)
    print(build(supervision_root=args.supervision, shard_root=args.features,
                market_manifest=args.market_manifest,
                market_ledger=args.market_ledger), flush=True)


if __name__ == '__main__':
    main()
