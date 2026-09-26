"""Publish cost-aware labels over an immutable certified market-feature shard."""
from __future__ import annotations

import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import sys
sys.dont_write_bytecode = True
from pathlib import Path
REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0,str(REPO))

import argparse
import math
from types import SimpleNamespace
import numpy as np
import polars as pl
from rich.console import Console

from research.rl_trading.v1.common import digest, file_hash
from research.rl_trading.v1.costs import from_plan
from research.rl_trading.v1.data import SessionShard
from research.rl_trading.v1.phase3_search import VERSION as PHASE3_VERSION
from research.rl_trading.v1.replay import replay_session
from research.rl_trading.v1.shard_labels import pack
from src.market_engine.level_book_store import read,write
from src.runtime_paths import runtime_root

VERSION = 'rl-trading-cost-label-overlay-v1'


def run(base_root: Path, phase3_root: Path):
    base = SessionShard(base_root)
    phase3_root = phase3_root.resolve()
    teacher = read(phase3_root/'plan.json')
    complete = read(phase3_root/'complete.json')
    old_teacher = read(Path(base.plan['phase3_root'])/'plan.json')
    if (teacher['version'] != PHASE3_VERSION or
            teacher['plan_hash'] != digest({k:v for k,v in teacher.items() if k != 'plan_hash'}) or
            complete['plan_hash'] != teacher['plan_hash'] or
            teacher['date'] != base.plan['date'] or
            teacher['phase2_plan_hash'] != base.plan['phase2_plan_hash'] or
            teacher['phase2_root'] != base.plan['phase2_root'] or
            teacher['v7_population'] != old_teacher['v7_population'] or
            teacher['first_us'] != old_teacher['first_us'] or
            teacher['end_us'] != old_teacher['end_us'] or
            not teacher.get('order_costs')):
        raise ValueError('Cost teacher differs from the certified market population')
    from_plan(teacher['order_costs'])
    for key,column in (('top_n','top_n'),('max_lots','max_lots'),
                       ('max_orders_per_second','max_orders'),
                       ('initial_cash','initial_cash'),('allocation_step','allocation_step')):
        if teacher['config'][key] != base.plan[column]:
            raise ValueError('Cost teacher changed the portfolio action grid: '+key)
    trajectory_path = phase3_root/'trajectory.parquet'
    if (file_hash(trajectory_path) != complete['files']['trajectory.parquet']['file_hash'] or
            file_hash(phase3_root/'positions_after.parquet') !=
            complete['files']['positions_after.parquet']['file_hash']):
        raise ValueError('Cost teacher files changed after certification')
    plan = {**base.plan}
    plan.pop('plan_hash')
    plan.update(version=VERSION,overlay_kind='cost_labels',
        base_shard_root=str(base.root),base_plan_hash=base.plan['plan_hash'],
        base_complete_hash=file_hash(base.root/'complete.json'),
        phase3_root=str(phase3_root),phase3_plan_hash=teacher['plan_hash'],
        phase3_complete_hash=file_hash(phase3_root/'complete.json'),
        order_costs=teacher['order_costs'],
        code_hashes={name:file_hash(Path(__file__).with_name(name)) for name in
            ('repack_cost_shards.py','shard_labels.py','costs.py','replay.py','data.py')})
    plan['plan_hash'] = digest(plan)
    runtime = runtime_root().resolve()
    if not runtime.is_dir():
        raise ValueError('Required runtime root is unavailable')
    root = runtime/'rl-trading-shards-cost-v1'/plan['date']/plan['plan_hash'][:20]
    root.mkdir(parents=True,exist_ok=True)
    already_complete = (root/'complete.json').exists()
    if already_complete:
        result = SessionShard(root)
        if result.plan != plan:
            raise ValueError('Existing cost overlay has a different plan')
        Console().print('Reused verified cost shard: '+str(root))
        return root
    else:
        write(root/'plan.json',plan)
    trajectory = pl.read_parquet(trajectory_path).to_dicts()
    from datetime import date
    from research.rl_trading.v1.common import bounds
    left,_ = bounds(date.fromisoformat(plan['date']))
    packed = pack(trajectory,base.arrays['features'],base.arrays['volume_60s'],
        base.arrays['execution'],plan['tickers'],left_us=left,top_n=plan['top_n'],
        max_lots=plan['max_lots'],max_orders=plan['max_orders'],
        allocation_step=plan['allocation_step'],initial_cash=plan['initial_cash'],
        min_volume=float(plan['liquidity_filter']['min_volume_60s']),
        min_trades=int(plan['liquidity_filter']['min_trades_60s']),
        order_costs=plan['order_costs'])
    files = {}
    for name in ('slots','rank','held_slots','actions','action_mask','lots','lot_slots',
                 'account','reward','return_to_go','done'):
        path = root/(name+'.npy')
        with path.open('wb') as stream:
            np.save(stream,packed[name],allow_pickle=False)
        files[path.name] = file_hash(path)
    result = SimpleNamespace(plan=plan,arrays={**base.arrays,**packed},
        complete={'rows':len(trajectory)})
    def prepare(state):
        index = state['index']
        cash,equity,_ = packed['account'][index]
        if (not math.isclose(float(cash)*plan['initial_cash'],state['cash'],abs_tol=.1) or
                not math.isclose(float(equity)*plan['initial_cash'],state['equity'],abs_tol=.1)):
            raise ValueError('Cost teacher account and closed-loop replay diverged')
        def select(step,mask,previous):
            if not np.array_equal(mask,packed['action_mask'][index,step]):
                raise ValueError('Cost teacher feasible mask and replay diverged')
            return int(packed['actions'][index,step])
        return select
    replay = replay_session(result,prepare)
    if (not replay['complete'] or
            not math.isclose(replay['profit'],complete['terminal_profit'],abs_tol=.1)):
        raise ValueError('Cost teacher and replay terminal profit differ')
    write(root/'teacher_replay_parity.json',dict(profit=replay['profit'],
        fees_paid=replay['fees_paid'],mask_and_state_parity=True))
    write(root/'complete.json',dict(plan_hash=plan['plan_hash'],rows=len(trajectory),
        teacher_optimality=teacher['optimality'],teacher_profit=complete['terminal_profit'],
        files=files,teacher_replay_parity_hash=file_hash(root/'teacher_replay_parity.json')))
    SessionShard(root)
    Console().print(f'Certified cost shard: {root} | profit ${replay["profit"]:,.2f} | '
        f'fees ${replay["fees_paid"]:,.2f}')
    return root


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-shard',type=Path,required=True)
    parser.add_argument('--phase3',type=Path,required=True)
    args = parser.parse_args(argv)
    run(args.base_shard,args.phase3)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
