"""Seal the forward-only train/development/test supervision inventory."""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import sys
sys.dont_write_bytecode = True
from pathlib import Path
REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0,str(REPO))

from datetime import date

from research.rl_trading.v1.common import digest,file_hash
from research.rl_trading.v1.dynamic_supervision import VERSION
from src.market_engine.level_book_store import read,write
from src.runtime_paths import runtime_root


TRAIN = [date(2026,7,30),date(2026,7,31)] + [
    date(2026,8,day) for day in (3,4,5,6,7,10,11,12,13,14,17,18,19,20,21)]
DEVELOPMENT = [date(2026,8,24),date(2026,8,25)]
TEST = [date(2026,8,26)]


def main():
    runtime=runtime_root().resolve()
    if not runtime.is_dir():
        raise ValueError('Required runtime root unavailable')
    base=runtime/'rl-trading-dynamic-supervision'
    inventory={}
    for split,days in (('train',TRAIN),('development',DEVELOPMENT),('test_sealed',TEST)):
        entries=[]
        for day in days:
            folder=base/str(day)
            valid=[]
            for root in folder.iterdir() if folder.is_dir() else ():
                if not (root/'complete.json').is_file():
                    continue
                plan=read(root/'plan.json')
                complete=read(root/'complete.json')
                if plan.get('scope')!='full_session':
                    continue
                if (plan.get('version')!=VERSION or plan.get('date')!=str(day) or
                        plan.get('split')!=split or
                        plan.get('plan_hash')!=digest({k:v for k,v in plan.items() if k!='plan_hash'}) or
                        complete.get('plan_hash')!=plan['plan_hash'] or
                        complete.get('seconds')!=57_481 or
                        file_hash(root/'orders.parquet')!=complete['orders_hash'] or
                        file_hash(Path(plan['phase3_root'])/'complete.json')!=plan['phase3_complete_hash'] or
                        file_hash(Path(plan['phase2_root'])/'complete.json')!=plan['phase2_complete_hash']):
                    raise ValueError(f'Invalid supervision certificate: {root}')
                valid.append(dict(date=str(day),root=str(root),plan_hash=plan['plan_hash'],
                    complete_hash=file_hash(root/'complete.json'),
                    orders=complete['orders'],positions=complete['positions']))
            if len(valid)!=1:
                raise ValueError(f'{day} requires exactly one complete full-session supervision root; found {len(valid)}')
            entries.append(valid[0])
        inventory[split]=entries
    if len(inventory['train'])!=17 or len(inventory['development'])!=2 or len(inventory['test_sealed'])!=1:
        raise ValueError('Forward split cardinality changed')
    plan=dict(version='rl-dynamic-forward-split-v1',train=inventory['train'],
        development=inventory['development'],test_sealed=inventory['test_sealed'],
        sealed_policy='Do not read August 26 teacher profit or order details for selection',
        observation_policy='Hindsight Phase 2 scores and teacher actions are labels, never causal features',
        code_hashes={name:file_hash(REPO/name) for name in (
            'research/rl_trading/v1/publish_dynamic_split.py',
            'research/rl_trading/v1/dynamic_supervision.py')})
    plan['plan_hash']=digest(plan)
    root=base/'splits'/plan['plan_hash'][:20]
    root.mkdir(parents=True,exist_ok=True)
    write(root/'manifest.json',plan)
    print(root)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
