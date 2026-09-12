"""Seed from a reviewed session book, calculate the next session and chart both."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import sys
sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import argparse
from datetime import datetime
import json
from math import prod
import time

from research.mlops.env import load_env_files
from research.mlops.clickhouse import discover_clickhouse_env_files
from src.backend.historical_session_level_source import load,_query
from src.backend.historical_session_level_chart import render
from src.market_engine.historical_session_levels import extract,Settings
from src.market_engine.historical_level_checkpoint import seed,consolidate,digest
from build_structure_book_clickhouse import canonical_splits


def checkpoint(path,value):
    if path.exists():
        if json.loads(path.read_text(encoding='utf-8'))!=value:
            raise ValueError(f'Existing checkpoint differs; select a new runtime: {path}')
        return
    tmp=path.with_suffix('.tmp')
    with tmp.open('w',encoding='utf-8') as f:
        json.dump(value,f,indent=2,allow_nan=False);f.flush();os.fsync(f.fileno())
    tmp.replace(path)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--seed-directory',type=Path,default=Path(r'D:\TradingML\runtimes\historical-session-levels\AAPL-2026-08-21'))
    p.add_argument('--next-session',default='2026-08-24')
    p.add_argument('--prior-checkpoint',type=Path,help='Continue an existing consolidated checkpoint instead of creating a seed')
    p.add_argument('--runtime',type=Path,default=Path(r'D:\TradingML\runtimes\historical-session-levels\AAPL-two-days'))
    args=p.parse_args();allowed=Path(r'D:\TradingML\runtimes').resolve();out=args.runtime.resolve()
    if not allowed.is_dir() or not out.is_relative_to(allowed):raise ValueError('Required runtime root unavailable or invalid')
    previous=json.loads((args.seed_directory/('next-extraction.json' if args.prior_checkpoint else 'levels.json')).read_text(encoding='utf-8'))
    prior_input=json.loads((args.seed_directory/('next-inputs.json' if args.prior_checkpoint else 'inputs.json')).read_text(encoding='utf-8'))
    if digest(dict(bars=prior_input['bars'],profile=prior_input['profile']))!=previous['input_sha256']:
        raise ValueError('Reviewed starting book input hash mismatch')
    if datetime.fromisoformat(args.next_session).date()<=datetime.fromisoformat(previous['session']).date():
        raise ValueError('Next session must follow seed session')
    ticker=previous['ticker'];load_env_files(discover_clickhouse_env_files(),verbose=False)
    # Reuse metadata validation for the symbol and date before composing SQL.
    from src.backend.swing_book_source import source_metadata,HISTORICAL_POLICY
    source_metadata(ticker,args.next_session,policy=HISTORICAL_POLICY)
    next_dates=_query(f"SELECT source_date FROM market_sip_compact.events_ordinal_continuity FINAL WHERE ticker='{ticker}' AND source_date>'{previous['session']}' ORDER BY source_date LIMIT 1")
    if not next_dates or next_dates[0]['source_date']!=args.next_session:raise ValueError('Requested session skips a certified session')
    split_rows=_query(f"SELECT execution_date,split_from,split_to,inserted_at FROM q_live.market_stock_split_v1 FINAL WHERE provider_ticker='{ticker}' AND execution_date>'{previous['session']}' AND execution_date<='{args.next_session}' ORDER BY execution_date")
    splits=canonical_splits(split_rows);factor=prod(float(s['split_from'])/float(s['split_to']) for s in splits)
    out.mkdir(parents=True,exist_ok=True)
    initial=json.loads(args.prior_checkpoint.read_text(encoding='utf-8')) if args.prior_checkpoint else seed(previous)
    if initial['session']!=previous['session'] or initial['ticker']!=ticker:raise ValueError('Prior checkpoint does not match reviewed previous session')
    checkpoint(out/f"checkpoint-{previous['session']}.json",initial)
    print(f'{ticker}: seed {len(initial["levels"])} levels from {previous["session"]}; next {args.next_session}; split factor {factor}',flush=True)
    start=time.perf_counter();bars,profile,source=load(ticker,args.next_session,progress=lambda d,t:print(f'Next session data: {d}/{t} windows',flush=True));read_time=time.perf_counter()-start
    start=time.perf_counter();today=extract(bars,profile,ticker=ticker,session=args.next_session,
        available_at=datetime.fromisoformat(source['end']).timestamp(),source=source,settings=Settings(**previous['settings']));extract_time=time.perf_counter()-start
    start=time.perf_counter();final=consolidate(initial,today,bars,profile,split_factor=factor,split_evidence=splits);merge_time=time.perf_counter()-start
    checkpoint(out/f'checkpoint-{args.next_session}.json',final)
    combined_profile={}
    for row in prior_input['profile']+profile:combined_profile[row['price']]=combined_profile.get(row['price'],0)+row['volume']
    combined=dict(today,session=f"{previous['session']} + {args.next_session}",sessions=[dict(session=previous['session'],start=previous['source']['start'],end=previous['source']['end']),dict(session=args.next_session,start=source['start'],end=source['end'])],
        source=dict(source,start=previous['source']['start']),levels=final['levels'],prior_level_count=len(initial['levels']),
        counts=dict(today['counts'],selected=len(final['levels'])),checkpoint_counts=final['counts'],input_sha256=digest([previous['input_sha256'],today['input_sha256']]))
    from copy import deepcopy
    combined['levels']=deepcopy(final['levels'])
    for level in combined['levels']:
        level['role_segments']=[s for s in level['role_segments'] if s['session'] in (previous['session'],args.next_session)]
    (out/'next-inputs.json').write_text(json.dumps(dict(bars=bars,profile=profile,source=source)),encoding='utf-8')
    (out/'next-extraction.json').write_text(json.dumps(today,indent=2),encoding='utf-8')
    (out/'chart-book.json').write_text(json.dumps(combined,indent=2),encoding='utf-8')
    (out/'chart.html').write_text(render(combined,prior_input['bars']+bars,[dict(price=k,volume=v) for k,v in sorted(combined_profile.items())]),encoding='utf-8')
    report=dict(counts=final['counts'],independent_extraction=today['counts'],timings=dict(read_seconds=read_time,extract_seconds=extract_time,consolidate_seconds=merge_time),
        seed_checkpoint_hash=initial['checkpoint_hash'],next_checkpoint_hash=final['checkpoint_hash'],split_factor=factor,chart=str(out/'chart.html'))
    (out/'validation.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report),flush=True)


if __name__=='__main__':main()
