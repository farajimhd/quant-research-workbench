"""Extract and chart a retrospective session, with no prior-level seed."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import sys
sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import argparse
from datetime import datetime
import json
import time

from research.mlops.env import load_env_files
from research.mlops.clickhouse import discover_clickhouse_env_files
from src.backend.historical_session_level_source import load
from src.market_engine.historical_session_levels import extract,Settings
from src.backend.historical_session_level_chart import render


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ticker',default='AAPL')
    parser.add_argument('--session',default='2026-08-21')
    parser.add_argument('--tick',type=float,default=.01)
    parser.add_argument('--runtime',type=Path,default=Path(r'D:\TradingML\runtimes\historical-session-levels'))
    args=parser.parse_args()
    allowed=Path(r'D:\TradingML\runtimes').resolve()
    root=args.runtime.resolve()
    if not root.is_relative_to(allowed) or not allowed.is_dir():
        raise ValueError('An available D:\\TradingML\\runtimes output directory is required')
    load_env_files(discover_clickhouse_env_files(),verbose=False)
    print(f'{args.ticker} {args.session}: reading certified canonical session; prior levels=0',flush=True)
    start=time.perf_counter();bars,profile,source=load(args.ticker,args.session,
        progress=lambda done,total:print(f'Canonical aggregation: {done}/{total} two-hour windows complete',flush=True))
    read_seconds=time.perf_counter()-start
    print(f'Read {len(bars):,} seconds and {len(profile):,} price-volume bins; extracting zones',flush=True)
    start=time.perf_counter()
    result=extract(bars,profile,ticker=args.ticker,session=args.session,
        available_at=datetime.fromisoformat(source['end']).timestamp(),source=source,settings=Settings(tick=args.tick))
    result['timings']=dict(read_seconds=read_seconds,extract_seconds=time.perf_counter()-start)
    out=root/f'{args.ticker}-{args.session}'
    out.mkdir(parents=True,exist_ok=True)
    # Preserve exact inputs for reproducible comparison without querying again.
    (out/'inputs.json').write_text(json.dumps(dict(bars=bars,profile=profile,source=source)),encoding='utf-8')
    (out/'levels.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    (out/'chart.html').write_text(render(result,bars,profile),encoding='utf-8')
    print(json.dumps(dict(output=str(out),counts=result['counts'],timings=result['timings'])),flush=True)


if __name__=='__main__':main()
