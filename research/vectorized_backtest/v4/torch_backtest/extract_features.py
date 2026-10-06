"""Separate SELECT-only V6-schema producer; never part of optimizer replay.

Writes only immutable feature files. No teacher/opportunity labels are built.
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
os.environ['POLARS_MAX_THREADS']='1'
from pathlib import Path
from datetime import date
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import get_context
from collections import deque
import argparse,json,time
import numpy as np
import polars as pl
from .runtime import require_runtime,write_json,file_hash

CLIENT=None
def ordered_features(pool,packets,maximum):
    """Bounded submission of this feature-only worker, never V6 label workers."""
    if maximum<1:raise ValueError('Positive feature queue bound required')
    packets=iter(packets);pending=deque()
    for _ in range(maximum):
        try:packet=next(packets)
        except StopIteration:break
        pending.append(pool.submit(work,packet))
    while pending:
        yield pending.popleft().result()
        try:packet=next(packets)
        except StopIteration:continue
        pending.append(pool.submit(work,packet))

def initialize():
    global CLIENT
    from research.rl_trading.v1 import arte_source
    CLIENT=arte_source.reader(threads=1)

def work(packet):
    day,prior_day,listing,current,prior=packet
    from research.rl_trading.v6.source import read_candles,read_previous_volume
    from research.rl_trading.v6.reference import read_reference
    from research.rl_trading.v6.features import encode,CandleFeatures
    bars,indicators=read_candles(CLIENT,current,day,listing['ticker'])
    if bars.is_empty():
        return listing['listing_id'],CandleFeatures(np.empty(0,dtype=np.int64),np.empty((0,37),dtype=np.float32),np.empty((0,2,5,11),dtype=np.float32))
    previous=read_previous_volume(CLIENT,prior,prior_day,listing['ticker']) if prior is not None and listing['ticker'] in prior['units'][str(prior_day)] else pl.DataFrame(schema={'bucket_index':pl.Int64,'volume':pl.Float64})
    seed,splits,fundamentals,evidence=read_reference(CLIENT,day,listing)
    return listing['listing_id'],encode(day,bars,indicators,previous,seed,splits,fundamentals)

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',type=Path,required=True);parser.add_argument('--previous-manifest',type=Path)
    parser.add_argument('--ledger',type=Path,required=True);parser.add_argument('--date',type=date.fromisoformat,required=True)
    parser.add_argument('--previous-date',type=date.fromisoformat);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--workers',type=int,default=32);args=parser.parse_args(argv)
    from research.mlops.clickhouse import discover_clickhouse_env_files
    from research.mlops.env import load_env_files
    from research.rl_trading.v1 import arte_source,reference_features
    from research.rl_trading.v1.common import digest,exclusive
    from research.rl_trading.v6.source import require_reporting_coverage
    from research.rl_trading.v6.universe_scope import scope_population
    from research.rl_trading.v6.census import one_second_counts
    from research.rl_trading.v6.config import worker_plan
    from research.rl_trading.v6.bank import write_bank
    from .source.arte_source import population as research_population
    from .encoding.config import DEFAULT_EXCLUDED_TICKERS
    import psutil
    if args.previous_date and args.previous_date>=args.date:raise ValueError('Previous feature day must precede current day')
    output=require_runtime(args.output);load_env_files(discover_clickhouse_env_files(),verbose=False)
    current=arte_source.load_build(args.manifest,args.ledger,[args.date])
    prior=arte_source.load_build(args.previous_manifest or args.manifest,args.ledger,[args.previous_date]) if args.previous_date else None
    require_reporting_coverage(current,args.date)
    if prior:require_reporting_coverage(prior,args.previous_date)
    reader=arte_source.reader(threads=1)
    try:
        storage=arte_source.storage_check(reader);reference_storage=reference_features.storage_check(reader)
        population,proof=research_population(reader,current,args.date,
            diagnostic_directory=output/'population-audit',excluded_tickers=DEFAULT_EXCLUDED_TICKERS,
            regular_us_exchanges_only=True)
        population,scope=scope_population(reader,population)
        counts=one_second_counts(reader,current,args.date,[r['ticker'] for r in population])
    finally:reader.close()
    mapping={r['listing_id']:r['ticker'] for r in population}
    lengths={key:counts[value] for key,value in sorted(mapping.items())}
    budget=worker_plan(requested=min(args.workers,len(population)),available_gib=psutil.virtual_memory().available/2**30)
    plan=dict(version='rl-trading-actual-candles-features-v6',consumer='vectorized-backtest-v4',day=str(args.date),previous_day=str(args.previous_date) if prior else None,
        source_build_id=current['build_id'],previous_build_id=prior['build_id'] if prior else None,source_units_hash=digest(current['units'][str(args.date)]),
        previous_units_hash=digest(prior['units'][str(args.previous_date)]) if prior else None,source_definition_hash=current['definition_hash'],population_snapshot_hash=proof['snapshot_hash'],universe_scope=scope,census=lengths,labels_generated=False)
    plan['hash']=digest(plan)
    with exclusive(output/'producer'):
        path=output/'plan.json'
        if path.exists() and json.loads(path.read_text())!=plan:raise ValueError('Existing feature producer identity differs')
        write_json(path,plan)
        if (output/'complete.json').exists():
            from .feature_bank import CertifiedBank
            CertifiedBank(output,expected_day=str(args.date));return 0
        by_identity={r['listing_id']:r for r in population}
        progress_path=output/'bank'/'progress.json'
        done=set(json.loads(progress_path.read_text())) if progress_path.exists() else set()
        packets=((args.date,args.previous_date,by_identity[key],current,prior) for key in sorted(mapping) if key not in done)
        began=time.perf_counter()
        with ProcessPoolExecutor(max_workers=budget.listing_workers,mp_context=get_context('spawn'),initializer=initialize) as pool:
            def rows():
                count=len(done)
                for identity,features in ordered_features(pool,packets,budget.max_in_flight):
                    if len(features.close_us)!=lengths[identity]:raise ValueError('Source candle census changed')
                    count+=1
                    if count%25==0:write_json(output/'progress.json',dict(status='extracting_features',day=str(args.date),completed_listings=count,total_listings=len(mapping),elapsed_seconds=time.perf_counter()-began))
                    yield identity,features
            bank=write_bank(output/'bank',lengths,rows(),source_hash=plan['hash'])
        write_json(output/'complete.json',dict(version=plan['version'],status='complete',plan_hash=plan['hash'],bank_file_hashes=bank['files_sha256'],storage=storage,reference_storage=reference_storage,labels_generated=False,elapsed_seconds=time.perf_counter()-began))
        write_json(output/'identity_map.json',dict(bank_certificate_sha256=file_hash(output/'complete.json'),listing_to_ticker=mapping,population_snapshot_hash=proof['snapshot_hash']))
        write_json(output/'progress.json',dict(status='complete',day=str(args.date),completed_listings=len(mapping),total_listings=len(mapping)))
    return 0

if __name__=='__main__':raise SystemExit(main())
