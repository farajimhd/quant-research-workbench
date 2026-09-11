#!/usr/bin/env python3
"""Verify every V6 survivor checkpoint and replay selected sessions causally."""
import os,sys
os.environ['PYTHONDONTWRITEBYTECODE']='1'
sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import argparse,json
from collections import defaultdict
from time import perf_counter
import prototype_structure_book_clickhouse as P
from research.mlops.env import discover_env_files,load_env_files
from src.backend.experimental_structure_book import resolve
from src.backend.swing_book_cursor import inputs
from src.backend.swing_book_source import session_bounds
from src.market_engine.swing_book_v6 import StreamingSwingBookV6,VERSION,FIELDS,LEGACY_FIELDS,matches_checkpoint


def validate(book,client,sessions):
    build=resolve(book)
    if build['version']!=VERSION:raise ValueError('Select a V6 survivor book')
    markers=client.query(f'SELECT * FROM {book}.sessions FINAL ORDER BY session_date','markers')
    expected={};levels_checked=0
    for offset in range(0,len(markers),10):
        batch=markers[offset:offset+10];by_stamp=defaultdict(list)
        stamps=','.join(str(int(m['closed_at']*1e6)) for m in batch)
        for row in client.query(f'SELECT valid_from_us,state_json FROM {book}.book FINAL WHERE valid_from_us IN ({stamps}) ORDER BY valid_from_us,level_id','states'):
            by_stamp[int(row['valid_from_us'])].append(json.loads(row['state_json']))
        for marker in batch:
            levels=by_stamp[int(marker['closed_at']*1e6)]
            if any(set(r) not in (set(FIELDS),set(LEGACY_FIELDS)) for r in levels):raise ValueError('Non-survivor fields persisted')
            seed=dict(version=VERSION,closed_at=float(marker['closed_at']),sequence=int(marker['sequence']),levels=levels)
            if P.digest(seed)!=marker['state_hash']:raise ValueError('Checkpoint integrity mismatch')
            restored=StreamingSwingBookV6(seed,seed['closed_at'])
            if not matches_checkpoint(restored.closing_state(seed['closed_at']),seed):raise ValueError('Survivor checkpoint not stable after restore')
            if len(restored.snapshot()['unified_levels'])!=len(levels):raise ValueError('Restored survivor is not qualified or is redundant')
            expected[marker['session_date']]=seed;levels_checked+=len(levels)
    replays=[]
    manifest=json.loads((Path(build['runtime'])/'source_manifest.json').read_text())
    split_days={s['execution_date'] for s in manifest[2] if s['execution_date'] in expected}
    for session in sorted(set(sessions)|split_days):
        seed,_,factor,bars=inputs(book,build['ticker'],session,build['fingerprint'])
        opening,closing=session_bounds(session)
        engine=StreamingSwingBookV6(seed,opening.timestamp(),factor)
        start=perf_counter()
        for bar in bars:engine.observe(*bar)
        if not matches_checkpoint(engine.closing_state(closing.timestamp()),expected[session]):raise ValueError('Causal replay does not match persisted close')
        replays.append(dict(session=session,bars=len(bars),compute_seconds=perf_counter()-start))
    return dict(book=book,ticker=build['ticker'],status='passed',checkpoints=len(markers),survivor_rows=levels_checked,replays=replays)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--books',nargs='+',required=True);p.add_argument('--sessions',nargs='+',required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--env-file',type=Path)
    a=p.parse_args()
    if not a.output.resolve().is_relative_to(Path(r'D:\TradingML\runtimes')):p.error('Use required runtime root')
    files=[a.env_file] if a.env_file else discover_env_files(Path.cwd())
    load_env_files(files,verbose=False);client=P.Client(files[0],2)
    results=[]
    for book in a.books:
        results.append(validate(book,client,a.sessions));P.save(a.output,results)
        print(json.dumps(results[-1]),flush=True)


if __name__=='__main__':main()
