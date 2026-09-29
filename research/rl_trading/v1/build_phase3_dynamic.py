"""Build a versioned, restartable V4 dynamic teacher from Phase 2 V7."""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import sys
sys.dont_write_bytecode = True
from pathlib import Path
REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0,str(REPO))

import argparse
from datetime import date
from hashlib import sha256

import numpy as np
import polars as pl

from research.rl_trading.v1.common import bounds,digest,exclusive,file_hash
from research.rl_trading.v1.costs import FixedOrderCosts
from research.rl_trading.v1.market_values import MarketValues
from research.rl_trading.v1.phase2_close_values import VERSION as PHASE2_VERSION
from research.rl_trading.v1.phase3_dynamic_teacher import (
    VERSION,Config,future_first_scores,run_stream)
from src.market_engine.level_book_store import read,write
from src.runtime_paths import runtime_root


def _parquet(path,frame):
    temporary = path.with_suffix('.parquet.tmp')
    frame.write_parquet(temporary,compression='zstd',statistics=True)
    temporary.replace(path)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase2',type=Path,required=True)
    parser.add_argument('--start-second',type=int,default=0)
    parser.add_argument('--end-second',type=int,required=True)
    parser.add_argument('--initial-cash',type=float,default=10_000.)
    parser.add_argument('--min-net-return',type=float,default=.01)
    parser.add_argument('--window-seconds',type=int,default=30)
    args=parser.parse_args(argv)
    config=Config(args.initial_cash,args.min_net_return,args.window_seconds)
    config.validate()
    if not 0 <= args.start_second <= args.end_second <= 57_480:
        parser.error('Require a segment within 04:00-19:58 ET')
    runtime=runtime_root().resolve()
    if not runtime.is_dir():
        raise ValueError('Required runtime root unavailable')
    source=args.phase2.resolve()
    if not source.is_relative_to(runtime):
        raise ValueError('Phase 2 source must be under the runtime root')
    parent=read(source/'plan.json')
    complete=read(source/'complete.json')
    if (parent.get('version') != PHASE2_VERSION or
            parent.get('plan_hash') != digest({k:v for k,v in parent.items() if k!='plan_hash'}) or
            complete.get('plan_hash') != parent['plan_hash']):
        raise ValueError('Complete Phase 2 V7 source required')
    day=date.fromisoformat(parent['date'])
    first=bounds(day)[0]
    times=np.arange(first+args.start_second*1_000_000,
                    first+(args.end_second+1)*1_000_000,1_000_000,dtype=np.int64)
    sources=('research/rl_trading/v1/build_phase3_dynamic.py',
             'research/rl_trading/v1/phase3_dynamic_teacher.py',
             'research/rl_trading/v1/costs.py',
             'research/rl_trading/v1/market_values.py')
    plan=dict(version=VERSION,phase2_root=str(source),phase2_plan_hash=parent['plan_hash'],
        date=parent['date'],start_second=args.start_second,end_second=args.end_second,
        scope='full_session' if args.start_second==0 and args.end_second==57_480 else 'bounded_segment',
        initial_cash=config.initial_cash,
        min_net_return=config.min_net_return,window_seconds=config.window_seconds,
        fee_model=FixedOrderCosts().plan(),
        optimality='approximate_normalized_window',
        code_hashes={p:sha256((REPO/p).read_text(encoding='utf-8').replace('\r\n','\n').encode()).hexdigest()
                     for p in sources})
    plan['plan_hash']=digest(plan)
    root=runtime/'hindsight-phase3-dynamic'/parent['date']/plan['plan_hash'][:20]
    root.mkdir(parents=True,exist_ok=True)
    with exclusive(root/'run.lock'):
        if (root/'complete.json').exists():
            saved=read(root/'complete.json')
            if saved.get('plan_hash')!=plan['plan_hash'] or any(
                    file_hash(root/name)!=hash_value for name,hash_value in saved['files'].items()):
                raise ValueError('Existing dynamic teacher artifact failed integrity check')
            print(root)
            return 0
        write(root/'plan.json',plan)
        progress_path=root/'progress.json'
        progress=read(progress_path) if progress_path.exists() else None
        if progress is not None and progress.get('plan_hash')!=plan['plan_hash']:
            raise ValueError('Dynamic teacher restart plan changed')
        resume=progress.get('state') if progress is not None else None
        opening=source/complete['tensor']['opening']['file']
        candidates=(pl.scan_parquet(opening).filter(
            (pl.col('side')=='long') & pl.col('time_us').is_between(int(times[0]),int(times[-1])))
            .select('time_us','episode_uid','can_open','open_value_per_dollar')
            .collect())
        future=future_first_scores(candidates,config,times)
        with MarketValues(source) as market:
            def snapshots():
                for time_us in times[0 if resume is None else resume['processed']:]:
                    yield market.at(int(time_us)).filter(pl.col('side')=='long')
            def checkpoint(state):
                write(progress_path,dict(plan_hash=plan['plan_hash'],
                    completed_seconds=state['processed'],total_seconds=len(times),
                    state=state),immutable=False)
            trajectory,positions,report=run_stream(times,snapshots(),future,config,
                resume=resume,on_checkpoint=checkpoint)
        _parquet(root/'trajectory.parquet',trajectory)
        _parquet(root/'positions.parquet',positions)
        files={name:file_hash(root/name) for name in ('trajectory.parquet','positions.parquet')}
        write(root/'complete.json',dict(plan_hash=plan['plan_hash'],files=files,
            trajectory_rows=trajectory.height,positions=positions.height,report=report))
        print(root)
        print(report)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
