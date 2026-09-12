"""Sequential, restart-safe historical books; bounded two-ticker campaigns."""
import argparse
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime
from math import prod
from pathlib import Path
import re
import time
from threading import Event

from research.mlops.env import load_env_files
from research.mlops.clickhouse import discover_clickhouse_env_files
from src.backend.historical_session_level_source import load,_query
from src.backend.swing_book_source import source_metadata,HISTORICAL_POLICY
from scripts.build_structure_book_clickhouse import canonical_splits
from src.market_engine.historical_session_levels import Settings
from src.market_engine.historical_level_checkpoint import digest
from src.market_engine.level_book_store import ROOT,read,write,verified_book
from dataclasses import asdict
import numpy as np
from src.market_engine.streaming_level_book import StreamingLevelBook,EXTRACTION_VERSION
from src.market_engine.reaction_band import CONFIG as BAND_CONFIG

STOP=Event()


def inputs(root,ticker,day,cache_roots):
    path=root/'inputs'/f'{day}.json.gz'
    revision=source_metadata(ticker,day,policy=HISTORICAL_POLICY)[0]
    if path.exists():
        value=read(path)
        if value['source']['revision']!=revision or value['content_hash']!=digest({k:v for k,v in value.items() if k!='content_hash'}):
            raise ValueError(f'Canonical input changed: {ticker} {day}')
        return value,'resumed'
    for cache in cache_roots:
        candidate=cache/'inputs'/f'{day}.json'
        if not candidate.exists():candidate=cache/'inputs'/f'{day}.json.gz'
        if not candidate.exists():continue
        manifest=read(cache/'manifest.json')
        if manifest.get('ticker')!=ticker:continue
        old=read(candidate)
        if old['content_hash']!=digest({k:v for k,v in old.items() if k!='content_hash'}):
            raise ValueError(f'Cached source checksum failed: {candidate}')
        if old['source']['revision']!=revision:raise ValueError(f'Cached source revision differs: {candidate}')
        value={k:old[k] for k in ('bars','profile','source')};mode='verified_cache';break
    else:
        bars,profile,source=load(ticker,day,window_hours=16)
        value=dict(bars=bars,profile=profile,source=source);mode='canonical_read'
    value['content_hash']=digest(value);write(path,value);return value,mode


def build(ticker,args):
    root=args.runtime/ticker;started=time.perf_counter()
    dates=[str(r['source_date']) for r in _query(f"SELECT DISTINCT source_date FROM market_sip_compact.events_ordinal_continuity FINAL WHERE ticker='{ticker}' AND source_date>='{args.start}' AND source_date<='{args.test_day}' ORDER BY source_date")]
    days=[d for d in dates if d<=args.end]
    if not days or args.test_day not in dates:raise ValueError(f'No certified build/test coverage for {ticker}')
    splits=canonical_splits(_query(f"SELECT execution_date,split_from,split_to,inserted_at FROM q_live.market_stock_split_v1 FINAL WHERE provider_ticker='{ticker}' AND execution_date>='{args.start}' AND execution_date<='{args.test_day}' ORDER BY execution_date"))
    plan=dict(version='level-book-v7-mle-1',extraction_version=EXTRACTION_VERSION,band_config=BAND_CONFIG,ticker=ticker,start=args.start,end=args.end,test_day=args.test_day,days=days,splits=splits,settings=asdict(Settings()),retrospective_history=True,streaming_test=True)
    write(root/'plan.json',plan)
    cache_roots=[p.parent for p in Path(r'D:\TradingML\runtimes\reaction-level-model').glob('*/manifest.json')]
    cache_roots.extend(p.parent for p in ROOT.glob('*/*/manifest.json') if p.parent!=root)
    prior=None;counts=dict(completed=0,resumed=0,empty=0,failed=0,total=len(days));timings=[]
    for day in days:
        if STOP.is_set():
            write(root/'status.json',dict(state='interrupted',counts=counts,next_session=day),immutable=False)
            return dict(state='interrupted',counts=counts)
        stage=time.perf_counter()
        write(root/'status.json',dict(state='running',active=day,queued=len(days)-counts['completed'],counts=counts,elapsed_seconds=time.perf_counter()-started),immutable=False)
        value,mode=inputs(root,ticker,day,cache_roots)
        checkpoint=root/'books'/f'{day}.json.gz';receipt=root/'receipts'/f'{day}.json'
        if receipt.exists():
            record=read(receipt)
            if record['input_hash']!=value['content_hash'] or record['prior_hash']!=(prior['checkpoint_hash'] if prior else None):raise ValueError('Resume provenance mismatch')
            if record['state']=='empty':counts['empty']+=1
            else:
                prior=verified_book(checkpoint)
                if prior['checkpoint_hash']!=record['checkpoint_hash']:raise ValueError('Receipt/checkpoint mismatch')
            counts['resumed']+=1
        else:
            prior_hash=prior['checkpoint_hash'] if prior else None
            if not value['bars']:
                record=dict(state='empty',reason='no_eligible_price_seconds',input_hash=value['content_hash'],prior_hash=prior_hash);counts['empty']+=1
            else:
                actions=[s for s in splits if prior and prior['session']<s['execution_date']<=day]
                factor=prod(float(s['split_from'])/float(s['split_to']) for s in actions)
                start=datetime.fromisoformat(value['source']['start']).timestamp();end=datetime.fromisoformat(value['source']['end']).timestamp()
                initial=prior
                if initial is None:
                    initial=dict(ticker=ticker,session='0001-01-01',available_at=start,levels=[],source_extraction_version=EXTRACTION_VERSION,band_config=BAND_CONFIG)
                    initial['checkpoint_hash']=digest(initial)
                ranges=np.array([b['high']-b['low'] for b in value['bars']]);lo=min(b['low'] for b in value['bars']);hi=max(b['high'] for b in value['bars'])
                tick=.0001 if lo<1 else .01;noise=float(np.median(ranges[ranges>0])) if np.any(ranges>0) else tick
                prominence=max(3*tick,6*noise,(hi-lo)*.05)
                engine=StreamingLevelBook(initial,ticker=ticker,session=day,start=start,end=end,split_factor=factor,split_evidence=actions,
                    settings=Settings(tick=tick),discovery_prominence=prominence)
                for bar in value['bars']:engine.update(bar,observed_at=bar['t'])
                prior=engine.historical_checkpoint(value['content_hash'])
                write(checkpoint,prior)
                record=dict(state='complete',input_hash=value['content_hash'],prior_hash=prior_hash,checkpoint_hash=prior['checkpoint_hash'],levels=sum(r['qualified'] for r in prior['levels']))
            write(receipt,record)
        counts['completed']+=1;elapsed=time.perf_counter()-stage;timings.append(elapsed)
        print(f"{ticker} {counts['completed']}/{len(days)} {day} {mode} {elapsed:.2f}s levels={len(prior['levels']) if prior else 0} empty={counts['empty']}",flush=True)
    if prior is None:raise ValueError(f'{ticker}: no usable historical book')
    value,mode=inputs(root,ticker,args.test_day,cache_roots)
    if not value['bars']:raise ValueError('Test session has no eligible price seconds')
    publish=dict(plan,ready=True,dates=[args.test_day],prior_session=prior['session'],prior_hash=prior['checkpoint_hash'],test_input_hash=value['content_hash'])
    write(root/'manifest.json',publish)
    report=dict(state='complete',counts=counts,levels=sum(r['qualified'] for r in prior['levels']),candidates=sum(not r['qualified'] for r in prior['levels']),elapsed_seconds=time.perf_counter()-started,session_seconds=timings)
    write(root/'status.json',report,immutable=False);return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--tickers',nargs='+',default=['JUNS','SUGP']);p.add_argument('--start',default='2025-01-01');p.add_argument('--end',default='2026-08-20');p.add_argument('--test-day',default='2026-08-21');p.add_argument('--runtime',type=Path,default=ROOT/'jan2025-aug2026-v2-mle')
    args=p.parse_args()
    if len(set(args.tickers))!=len(args.tickers) or not 1<=len(args.tickers)<=2 or any(not re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,19}',t) for t in args.tickers):raise ValueError('Choose one or two distinct ticker symbols')
    for d in (args.start,args.end,args.test_day):datetime.strptime(d,'%Y-%m-%d')
    if not args.start<=args.end<args.test_day:raise ValueError('Historical period must precede test day')
    if not ROOT.parent.is_dir() or not args.runtime.resolve().is_relative_to(ROOT.resolve()):raise ValueError('Required level-book runtime root unavailable or invalid')
    load_env_files(discover_clickhouse_env_files(),verbose=False)
    failures=[]
    pool=ThreadPoolExecutor(max_workers=2)
    try:
        jobs={pool.submit(build,t,args):t for t in args.tickers}
        for job in as_completed(jobs):
            ticker=jobs[job]
            try:print(ticker,job.result(),flush=True)
            except Exception as exc:
                failures.append(ticker)
                path=args.runtime/ticker/'status.json';status=read(path) if path.exists() else {}
                write(path,dict(status,state='failed',reason=str(exc)),immutable=False);print(f'{ticker} FAILED: {exc}',flush=True)
    except KeyboardInterrupt:
        STOP.set();print('Stopping after each active session checkpoint; rerun the same command to resume.',flush=True)
    finally:pool.shutdown(wait=True)
    if failures:raise SystemExit('Failed tickers: '+', '.join(failures))


if __name__=='__main__':main()
