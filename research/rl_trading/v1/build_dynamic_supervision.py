"""Publish hash-bound sized action labels without rebuilding market features."""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import sys
sys.dont_write_bytecode = True
from pathlib import Path
REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0,str(REPO))

import argparse
from datetime import date

import polars as pl

from research.rl_trading.v1.common import bounds,digest,exclusive,file_hash
from research.rl_trading.v1.dynamic_supervision import VERSION,order_labels
from research.rl_trading.v1.phase2_close_values import VERSION as PHASE2_VERSION
from research.rl_trading.v1.phase3_dynamic_teacher import VERSION as PHASE3_VERSION
from src.market_engine.level_book_store import read,write
from src.runtime_paths import runtime_root


def split_for_day(day: date) -> str:
    if date(2026,7,30) <= day <= date(2026,8,21):
        return 'train'
    if day in (date(2026,8,24),date(2026,8,25)):
        return 'development'
    if day == date(2026,8,26):
        return 'test_sealed'
    raise ValueError('Session is outside the pinned forward split')


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase3',type=Path,required=True)
    parser.add_argument('--allow-segment',action='store_true',help='Only for bounded canaries')
    args=parser.parse_args(argv)
    runtime=runtime_root().resolve()
    if not runtime.is_dir():
        raise ValueError('Required runtime root unavailable')
    phase3=args.phase3.resolve()
    if not phase3.is_relative_to(runtime):
        raise ValueError('Teacher must be under the runtime root')
    teacher=read(phase3/'plan.json')
    certified=read(phase3/'complete.json')
    if (teacher.get('version')!=PHASE3_VERSION or
            teacher.get('plan_hash')!=digest({k:v for k,v in teacher.items() if k!='plan_hash'}) or
            certified.get('plan_hash')!=teacher['plan_hash'] or
            any(file_hash(phase3/name)!=expected for name,expected in certified['files'].items())):
        raise ValueError('Verified complete dynamic Phase 3 teacher required')
    source=Path(teacher['phase2_root']).resolve()
    if not source.is_relative_to(runtime):
        raise ValueError('Phase 2 root must be under the runtime root')
    phase2=read(source/'plan.json')
    phase2_complete=read(source/'complete.json')
    if (phase2.get('version')!=PHASE2_VERSION or
            phase2.get('plan_hash')!=teacher['phase2_plan_hash'] or
            phase2.get('plan_hash')!=digest({k:v for k,v in phase2.items() if k!='plan_hash'}) or
            phase2_complete.get('plan_hash')!=phase2['plan_hash'] or
            phase2['date']!=teacher['date'] or
            phase2['scope']!='certified_market_day_build_population' and not args.allow_segment):
        raise ValueError('Matching complete market-wide Phase 2 V7 required')
    day=date.fromisoformat(teacher['date'])
    split=split_for_day(day)
    first=bounds(day)[0]
    full=(teacher['start_second']==0 and teacher['end_second']==57_480)
    if not full and not args.allow_segment:
        raise ValueError('Training and test supervision require a full session')
    plan=dict(version=VERSION,date=str(day),split=split,
        scope='full_session' if full else 'bounded_segment',
        phase2_root=str(source),phase2_plan_hash=phase2['plan_hash'],
        phase2_complete_hash=file_hash(source/'complete.json'),
        phase3_root=str(phase3),phase3_plan_hash=teacher['plan_hash'],
        phase3_complete_hash=file_hash(phase3/'complete.json'),
        phase3_trajectory_hash=certified['files']['trajectory.parquet'],
        phase3_positions_hash=certified['files']['positions.parquet'],
        causal_feature_policy='reuse separately certified arte feature banks; do not expose Phase 2 hindsight scores as observations',
        action_contract=('ordered sell-before-buy actions with fractional quantities, '
            'pre-buy allocation weights and autoregressive remaining-cash weights'),
        code_hashes={name:file_hash(REPO/name) for name in (
            'research/rl_trading/v1/build_dynamic_supervision.py',
            'research/rl_trading/v1/dynamic_supervision.py')})
    plan['plan_hash']=digest(plan)
    root=runtime/'rl-trading-dynamic-supervision'/str(day)/plan['plan_hash'][:20]
    root.mkdir(parents=True,exist_ok=True)
    with exclusive(root/'run.lock'):
        complete_path=root/'complete.json'
        if complete_path.exists():
            previous=read(complete_path)
            if (previous.get('plan_hash')!=plan['plan_hash'] or
                    file_hash(root/'orders.parquet')!=previous['orders_hash']):
                raise ValueError('Existing supervision changed')
            print(root)
            return 0
        trajectory=pl.read_parquet(phase3/'trajectory.parquet')
        positions=pl.read_parquet(phase3/'positions.parquet')
        expected_first=first+teacher['start_second']*1_000_000
        expected_end=first+teacher['end_second']*1_000_000
        if (trajectory.height != teacher['end_second']-teacher['start_second']+1 or
                int(trajectory['time_us'][0])!=expected_first or
                int(trajectory['time_us'][-1])!=expected_end or
                trajectory['time_us'].n_unique()!=trajectory.height):
            raise ValueError('Teacher trajectory lacks a complete decision grid')
        orders=order_labels(trajectory,positions,float(teacher['initial_cash']))
        write(root/'plan.json',plan)
        temporary=root/'orders.parquet.tmp'
        orders.write_parquet(temporary,compression='zstd',statistics=True)
        temporary.replace(root/'orders.parquet')
        write(complete_path,dict(plan_hash=plan['plan_hash'],split=split,
            scope=plan['scope'],seconds=trajectory.height,positions=positions.height,
            orders=orders.height,orders_hash=file_hash(root/'orders.parquet')))
        print(root)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
