"""Publish a versioned current-mark account overlay for certified ARTE shards.

The feature, execution, label, and V7 arrays remain in the verified immutable
source shard. Only the account observation is recomputed from current prices.
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
from datetime import date
from types import SimpleNamespace

import numpy as np

from research.rl_trading.v1.common import bounds, digest, file_hash
from research.rl_trading.v1.data import SessionShard
from research.rl_trading.v1.replay import replay_session
from src.market_engine.level_book_store import read, write
from src.runtime_paths import runtime_root

VERSION = 'rl-trading-current-account-overlay-v1'


def mark_accounts(source: SessionShard, destination: Path, *, chunk: int = 4096) -> None:
    arrays = source.arrays
    rows = source.complete['rows']
    left_us = bounds(date.fromisoformat(source.plan['date']))[0]
    initial_cash = float(source.plan['initial_cash'])
    output = np.lib.format.open_memmap(destination,mode='w+',dtype=np.float32,
        shape=(rows,3))
    for start in range(0,rows,chunk):
        stop = min(rows,start+chunk)
        count = stop-start
        seconds = ((arrays['time_us'][start:stop]-left_us)//1_000_000).astype(np.int64)
        if np.any(seconds < 0) or np.any(seconds >= arrays['execution'].shape[1]):
            raise ValueError('Account mark time is outside the execution bank')
        cash = np.asarray(arrays['account'][start:stop,0],dtype=np.float64)*initial_cash
        equity = cash.copy()
        slots = np.asarray(arrays['slots'][start:stop])
        lot_slots = np.asarray(arrays['lot_slots'][start:stop])
        lots = np.asarray(arrays['lots'][start:stop])
        for lot_index in range(source.plan['max_lots']):
            present = lot_slots[:,lot_index] >= 0
            if not present.any():
                continue
            sample = np.nonzero(present)[0]
            ticker = slots[sample,lot_slots[sample,lot_index]]
            if np.any(ticker < 0) or np.any(~arrays['closeable'][ticker,seconds[sample]]):
                raise ValueError('Held lot lacks a current liquidation price')
            price = np.asarray(arrays['execution'][ticker,seconds[sample],1],dtype=np.float64)
            quantity = np.asarray(lots[sample,lot_index,0],dtype=np.float64)
            equity[sample] += quantity*price
        if np.any(~np.isfinite(equity)) or np.any(equity < 0):
            raise ValueError('Current account mark is invalid')
        output[start:stop,0] = arrays['account'][start:stop,0]
        output[start:stop,1] = equity/initial_cash
        output[start:stop,2] = arrays['account'][start:stop,2]
    output.flush()
    del output


def verify_teacher_replay(source: SessionShard, account_path: Path) -> dict:
    arrays = dict(source.arrays)
    arrays['account'] = np.load(account_path,mmap_mode='r',allow_pickle=False)
    shard = SimpleNamespace(plan=source.plan,complete=source.complete,arrays=arrays)
    top_n = source.plan['top_n']
    scale = source.plan['initial_cash']

    def teacher(state):
        index = state['index']
        expected = arrays['slots'][index]
        actual = np.full(top_n,-1,dtype=np.int32)
        actual[:len(state['visible'])] = state['visible']
        if (not np.array_equal(actual,expected) or
                abs(arrays['account'][index,0]-state['cash']/scale) > 2e-4 or
                abs(arrays['account'][index,1]-state['equity']/scale) > 2e-4):
            raise ValueError(f'Teacher state and execution replay differ at second {index}')

        def select(step,mask,previous):
            if not np.array_equal(mask,arrays['action_mask'][index,step]):
                raise ValueError(f'Teacher action mask differs at second {index}, order {step}')
            return int(arrays['actions'][index,step])
        return select

    result = replay_session(shard,teacher)
    if (not result['complete'] or
            not np.isclose(result['profit'],source.complete['teacher_profit'],rtol=0,atol=1e-5)):
        raise ValueError('Teacher trajectory profit does not reproduce the certificate')
    return dict(profit=result['profit'],seconds=result['seconds'],
        account_clock='current_completed_second',mask_and_state_parity=True)


def repack(root: Path) -> Path:
    source = SessionShard(root)
    if (source.plan.get('version') != 'rl-trading-structural-shards-v3' or
            source.plan.get('base_shard_root')):
        raise ValueError('Account overlay requires a certified original V3 shard')
    runtime = runtime_root().resolve()
    if not runtime.is_dir():
        raise ValueError('Required runtime root is unavailable')
    plan = dict(source.plan)
    plan.pop('plan_hash')
    plan.update(version=VERSION,account_clock='current_completed_second',
        base_shard_root=str(source.root),base_plan_hash=source.plan['plan_hash'],
        base_complete_hash=file_hash(source.root/'complete.json'),
        overlay_code_hashes={name:file_hash(Path(__file__).with_name(name))
            for name in ('repack_shards.py','replay.py','data.py')})
    plan['plan_hash'] = digest(plan)
    output = runtime/'rl-trading-shards-v4'/plan['date']/plan['plan_hash'][:20]
    output.mkdir(parents=True,exist_ok=True)
    plan_path = output/'plan.json'
    if plan_path.exists() and read(plan_path) != plan:
        raise ValueError('Existing overlay path has a different plan')
    write(plan_path,plan)
    complete_path = output/'complete.json'
    if complete_path.exists():
        SessionShard(output)
        return output
    temporary = output/'account.npy.tmp'
    mark_accounts(source,temporary)
    parity = verify_teacher_replay(source,temporary)
    os.replace(temporary,output/'account.npy')
    complete = dict(plan_hash=plan['plan_hash'],rows=source.complete['rows'],
        teacher_profit=source.complete['teacher_profit'],
        teacher_optimality=source.complete['teacher_optimality'],
        teacher_replay_parity=parity,
        files={'account.npy':file_hash(output/'account.npy')})
    write(complete_path,complete)
    SessionShard(output,verify=False)
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-shards',type=Path,nargs='+',required=True)
    args = parser.parse_args(argv)
    for source in args.source_shards:
        print(repack(source),flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
