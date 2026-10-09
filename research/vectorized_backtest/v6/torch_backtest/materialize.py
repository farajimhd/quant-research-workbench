"""Bounded, SELECT-only training preparation; price gate precedes volume reads.

Materializes causal top-N execution rows and a sparse backing store. Validation
is never opened. Missing prior-day certification fails that session closed.
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
os.environ.setdefault('POLARS_MAX_THREADS', '2')
import argparse
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
import json
from pathlib import Path
from time import time
import numpy as np
import polars as pl
from .runtime import file_hash, require_runtime
from .source import arte_source, arte_sql as sql
from .availability import configure_reader

VERSION = 'v6-volume-market-blocks-v2'
BAR_SCHEMA = dict(ticker=pl.String, clock=pl.Int64, close=pl.Float64,
    high=pl.Float64, low=pl.Float64, volume=pl.Float64, trade_count=pl.UInt64,
    execution_volume=pl.Float64, execution_notional=pl.Float64,
    price_valid=pl.UInt8, extremes_valid=pl.UInt8)


@contextmanager
def owned_run(root, *, version=VERSION):
    lock=root/'owner.lock'
    with lock.open('x',encoding='utf-8') as stream:
        json.dump(dict(pid=os.getpid(),version=version,started_epoch=time()),stream)
    try:yield
    finally:lock.unlink()


def write_json(path, value):
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(value, sort_keys=True, default=str), encoding='utf-8')
    temporary.replace(path)


def rolling_volume(volume, window):
    """Dense elapsed-second grid; current completed second included."""
    if window < 1 or volume.ndim != 2 or not np.isfinite(volume).all() or (volume < 0).any():
        raise ValueError('Invalid rolling-volume input')
    sums = np.cumsum(volume, axis=0, dtype=np.float64)
    result = sums.copy()
    result[window:] -= sums[:-window]
    return result


def stable_top_indices(scores,top_n,*,clock_batch=1024):
    """Exact score-descending/identity-ascending top K, without a full sort.

    Partition only finds the cutoff. Explicit tie ranks then select the lowest
    identities at that cutoff, preserving the full stable-sort reference.
    Clock chunks bound partition and tie scratch space independently of T.
    """
    if scores.ndim!=2 or top_n<1 or clock_batch<1:raise ValueError('Invalid top-K ranking dimensions')
    clocks,listings=scores.shape;k=min(top_n,listings)
    indices=np.full((clocks,k),-1,dtype=np.int64);values=np.full((clocks,k),-np.inf,dtype=np.float64)
    if not k:return indices,values
    for begin in range(0,clocks,clock_batch):
        block=scores[begin:begin+clock_batch]
        if np.isnan(block).any() or np.isposinf(block).any():raise ValueError('Non-finite ranked volume')
        cutoff=np.partition(block,listings-k,axis=1)[:,listings-k].copy()
        greater=block>cutoff[:,None]
        tied=(block==cutoff[:,None])&np.isfinite(cutoff[:,None])
        needed=k-greater.sum(1)
        selected=greater|(tied&(np.cumsum(tied,axis=1,dtype=np.int32)<=needed[:,None]))
        counts=selected.sum(1);rows,columns=np.nonzero(selected)
        starts=np.cumsum(counts)-counts
        slots=np.arange(len(columns))-np.repeat(starts,counts)
        candidates=np.full((len(block),k),-1,dtype=np.int64)
        candidates[rows,slots]=columns
        candidate_scores=np.where(candidates>=0,np.take_along_axis(block,candidates.clip(0),axis=1),-np.inf)
        order=np.lexsort((candidates,-candidate_scores),axis=1)
        indices[begin:begin+len(block)]=np.take_along_axis(candidates,order,axis=1)
        values[begin:begin+len(block)]=np.take_along_axis(candidate_scores,order,axis=1)
    return indices,values


def rank_indices(volume, available, window, top_n):
    if (available is not None and (available.shape!=volume.shape or available.dtype!=np.bool_)) or top_n < 1:
        raise ValueError('Invalid availability/top-N')
    scores = rolling_volume(volume, window)
    scores[scores<=0]=-np.inf
    if available is not None:scores[~available]=-np.inf
    return stable_top_indices(scores,top_n)


def prior_session(day):
    import pandas_market_calendars as mcal
    current = date.fromisoformat(day)
    schedule = mcal.get_calendar('XNYS').schedule(start_date=current-timedelta(days=15), end_date=current)
    earlier = schedule[schedule.index.date < current]
    if earlier.empty:
        raise ValueError('Previous exchange session unavailable')
    row = earlier.iloc[-1]
    return str(earlier.index[-1].date()), row['market_open'].to_pydatetime(), row['market_close'].to_pydatetime()


def prior_close_factors(split,mapping,previous,day):
    """Price context is independent of whether a bank has an RVOL denominator."""
    from .splits import price_factor
    return {mapping[key]:price_factor(evidence['splits'],previous,day)
            for key,evidence in split['listings'].items()}


def scope(source, day, tickers, stage):
    attempts = ','.join(f"({sql.literal(t)},toUUID({sql.literal(source['units'][day][t][stage]['attempt_id'])}))" for t in tickers)
    return (f"build_id={sql.literal(source['build_id'])} AND session_date=toDate({sql.literal(day)}) "
            f"AND (ticker,attempt_id) IN ({attempts})")


def prepare(item, args):
    day = item['day']; folder = args.output / day; folder.mkdir(exist_ok=True)
    identity = dict(version=VERSION, session=item, sessions_sha256=file_hash(args.sessions),
                    window=args.window, top_n=args.top_n, minimum_close=args.minimum_close,
                    maximum_close=args.maximum_close, block_seconds=args.block_seconds,
                    source_manifest_sha256=file_hash(item['source_manifest']),
                    prior_close_context_sha256=file_hash(args.prior_close_context) if args.prior_close_context else None,
                    implementation_sha256=file_hash(Path(__file__)))
    complete = folder / 'complete.json'
    if complete.exists():
        saved = json.loads(complete.read_text())
        if saved['identity'] != identity or any(file_hash(folder/name) != checksum for name, checksum in saved['files'].items()):
            raise ValueError('Exact materialization resume identity/hash mismatch')
        return saved
    started = time(); previous, opened, closed = prior_session(day)
    manifest = Path(item['source_manifest'])
    definition = json.loads(manifest.read_text())['definition']
    previous_manifest = manifest
    context=None
    if previous not in definition['plan']['requested']:
        if args.prior_close_context:
            from .source.common import digest
            from .previous_close import VERSION as CONTEXT_VERSION
            context=json.loads(args.prior_close_context.read_text())
            payload={k:v for k,v in context.items() if k!='hash'}
            if (context.get('hash')!=digest(payload) or context.get('version')!=CONTEXT_VERSION
                or context.get('status')!='complete' or context['previous_day']!=previous
                or context['current_day']!=day or context['sessions_sha256']!=file_hash(args.sessions)
                or context['source_manifest_sha256']!=file_hash(manifest) or context['validation_opened']):
                raise ValueError('Canonical closing-context identity/hash mismatch')
        elif args.context_manifest is None:
            raise ValueError(f'{day}: previous regular session {previous} requires certified --context-manifest')
        else:previous_manifest = args.context_manifest
    source = arte_source.load_build(manifest, item['source_ledger'], [day])
    prior = None if context else arte_source.load_build(previous_manifest, args.context_ledger or item['source_ledger'], [previous])
    reader = arte_source.reader(threads=1)
    files = {}
    try:
        arte_source.storage_check(reader)
        members, population = arte_source.population(reader, source, date.fromisoformat(day), regular_us_exchanges_only=True)
        if context:
            if context['population']!=population:raise ValueError('Closing context population changed')
            old_members=context['matched'];old_population=dict(context_sha256=file_hash(args.prior_close_context),scope=context['identity_scope'])
        else:old_members, old_population = arte_source.population(reader, prior, date.fromisoformat(previous), regular_us_exchanges_only=True)
        old = {row['listing_id']:row['ticker'] for row in old_members}
        paired = [row for row in members if old.get(row['listing_id']) == row['ticker']]
        from .encoding.clickhouse import _validate_units
        _validate_units(reader, source, date.fromisoformat(day), [r['ticker'] for r in members], None)
        if prior:_validate_units(reader, prior, date.fromisoformat(previous), [r['ticker'] for r in paired], None)
        # Certified per-listing split factors, already available at premarket opening.
        mapping = json.loads(Path(item['identity_map']).read_text())['listing_to_ticker']
        from .feature_bank import CertifiedBank
        from .splits import load_basis
        bank = CertifiedBank(item['feature_root'], expected_day=day)
        load_basis(item['split_certificate'], bank, None, mapping, rvol_only=True)
        split = json.loads(Path(item['split_certificate']).read_text())
        factors = {mapping[k]:v['rvol_price_factor'] for k,v in split['listings'].items()}
        if split['day'] != day or set(factors) != set(mapping.values()):
            raise ValueError('Incomplete opening split identity coverage')
        unsupported=[dict(**row,reason='certified_feature_and_opening_split_identity_unavailable') for row in paired if row['ticker'] not in factors]
        paired=[row for row in paired if row['ticker'] in factors]
        factors=prior_close_factors(split,mapping,previous,day)
        origin = int(datetime.fromisoformat(previous+'T00:00:00').replace(tzinfo=opened.tzinfo).timestamp())
        # Derive bucket bounds in source timezone via explicit UTC epoch offset.
        from zoneinfo import ZoneInfo
        origin = int(datetime.fromisoformat(previous+'T00:00:00').replace(tzinfo=ZoneInfo('America/New_York')).timestamp())
        low, high = int(opened.timestamp())-origin, int(closed.timestamp())-origin
        closes = {}
        if context:
            allowed={row['ticker'] for row in paired}
            closes={row['ticker']:float(row['closing'][0])/10000.*factors[row['ticker']]
                for row in context['rows'] if row['ticker'] in allowed and row['eligible_trades']>0}
        for offset in range(0, 0 if context else len(paired), 128):
            names = [r['ticker'] for r in paired[offset:offset+128]]
            rows = sql.query(reader, 'SELECT ticker,argMax(close_int,bucket_index)/10000. AS prior_close '
                f"FROM arte.bars_v1 WHERE {scope(prior,previous,names,'bars')} AND resolution_ms=1000 "
                f'AND bucket_index>={low} AND bucket_index<{high} AND price_valid=1 GROUP BY ticker')
            closes.update({r['ticker']:float(r['prior_close'])*factors[r['ticker']] for r in rows})
        eligible = sorted([r for r in paired if args.minimum_close <= closes.get(r['ticker'],np.nan) <= args.maximum_close], key=lambda r:r['listing_id'])
        if not eligible:
            raise ValueError('No certified price-eligible listings')
        write_json(folder/'eligibility.json', dict(identity=identity, previous_day=previous,
            prior_source=prior['build_id'] if prior else context['version'], population=population, previous_population=old_population,
            unsupported=unsupported,
            counts=dict(population=len(members), identity_unavailable=len(members)-len(paired)-len(unsupported),
                        opening_context_unavailable=len(unsupported),
                        missing_close=len(paired)-len(closes), eligible=len(eligible),
                        price_rejected=len(closes)-len(eligible)), listings=eligible, adjusted_prior_close=closes))
        start=int(datetime.fromisoformat(item['start']).timestamp());end=int(datetime.fromisoformat(item['end']).timestamp())
        clocks=np.arange(start,end,dtype=np.int64)+1
        day_origin=int(datetime.fromisoformat(day+'T00:00:00').replace(tzinfo=ZoneInfo('America/New_York')).timestamp())
        columns={row['ticker']:i for i,row in enumerate(eligible)}
        volume=np.zeros((len(clocks),len(eligible)),dtype=np.float64)
        pieces=[]
        # Price gate has completed: no rejected listing enters these volume reads.
        for offset in range(0,len(eligible),64):
            names=[r['ticker'] for r in eligible[offset:offset+64]]
            statement=(f'SELECT ticker,toInt64(bucket_index)+1+{day_origin} AS clock,close_int/10000. AS close,'
                'high_int/10000. AS high,low_int/10000. AS low,volume,trade_count,execution_volume,execution_notional,price_valid,extremes_valid '
                f"FROM arte.bars_v1 WHERE {scope(source,day,names,'bars')} AND resolution_ms=1000 "
                f'AND bucket_index>={start-day_origin} AND bucket_index<{end-day_origin} ORDER BY ticker,bucket_index')
            frame=arte_source.frame(reader,statement,BAR_SCHEMA)
            if frame.height:
                frame=frame.with_columns(pl.col('ticker').replace_strict(columns).cast(pl.Int32).alias('listing'))
                rows=frame['clock'].to_numpy()-clocks[0];cols=frame['listing'].to_numpy()
                if np.unique(rows*len(eligible)+cols).size != len(rows):raise ValueError('Duplicate source row')
                volume[rows,cols]=frame['volume'].to_numpy()
                pieces.append(frame)
        backing=pl.concat(pieces).sort(['clock','listing'])
        backing.write_parquet(folder/'backing.parquet')
        # Every eligible identity has an opening-known prior close. A missing
        # current price candle must not erase its trailing elapsed-volume rank.
        indices,scores=rank_indices(volume,None,args.window,args.top_n)
        np.save(folder/'clocks.npy',clocks,allow_pickle=False)
        np.save(folder/'top_indices.npy',indices.astype(np.int32),allow_pickle=False)
        for begin in range(0,len(clocks),args.block_seconds):
            stop=min(len(clocks),begin+args.block_seconds);idx=indices[begin:stop]
            selection=pl.DataFrame(dict(clock=np.repeat(clocks[begin:stop],idx.shape[1]),
                slot=np.tile(np.arange(idx.shape[1]),stop-begin),listing=idx.ravel(),rolling_volume=scores[begin:stop].ravel())).filter(pl.col('listing')>=0)
            block=selection.join(backing,on=['clock','listing'],how='left',validate='1:1').sort(['clock','slot'])
            name=f'block_{begin//args.block_seconds:04d}.parquet';block.write_parquet(folder/name);files[name]=file_hash(folder/name)
        for name in ('eligibility.json','backing.parquet','clocks.npy','top_indices.npy'):files[name]=file_hash(folder/name)
        saved=dict(status='complete',identity=identity,day=day,files=files,clocks=len(clocks),eligible_listings=len(eligible),
            source_rows=backing.height,compact_rows=int((indices>=0).sum()),elapsed_seconds=time()-started,
            feature_root=item['feature_root'],split_certificate=item['split_certificate'],validation_opened=False,
            ready_for_replay=False,remaining='Causal feature/history and quote/execution binding required')
        write_json(complete,saved);return saved
    finally:reader.close()


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sessions',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--workers',type=int,default=2);p.add_argument('--window',type=int,default=30)
    p.add_argument('--top-n',type=int,default=10);p.add_argument('--minimum-close',type=float,default=.8)
    p.add_argument('--maximum-close',type=float,default=50);p.add_argument('--block-seconds',type=int,default=300)
    p.add_argument('--context-manifest',type=Path);p.add_argument('--context-ledger',type=Path)
    p.add_argument('--prior-close-context',type=Path)
    p.add_argument('--only-days',nargs='+',help='Prepare an explicit subset; never claim full training readiness')
    args=p.parse_args(argv);args.output=require_runtime(args.output)
    if not 1<=args.workers<=4 or min(args.window,args.top_n,args.block_seconds)<1 or not 0<args.minimum_close<=args.maximum_close:
        raise ValueError('Invalid bounded materialization arguments')
    configure_reader(Path(__file__).resolve().parents[4])
    spec=json.loads(args.sessions.read_text());days=[s['day'] for s in spec['training']]
    if len(days)!=30 or len(set(days))!=30 or set(days)&{s['day'] for s in spec['validation']}:
        raise ValueError('Exactly 30 disjoint training dates required')
    requested=spec['training']
    if args.only_days:
        if len(set(args.only_days))!=len(args.only_days) or set(args.only_days)-set(days):raise ValueError('Invalid training subset')
        requested=[item for item in requested if item['day'] in args.only_days]
    results=[];failures=[]
    with owned_run(args.output), ThreadPoolExecutor(max_workers=args.workers) as pool:
        jobs={pool.submit(prepare,item,args):item['day'] for item in requested}
        for future in as_completed(jobs):
            try:result=future.result();results.append(result);event=dict(day=jobs[future],status='complete',elapsed_seconds=result['elapsed_seconds'])
            except Exception as error:event=dict(day=jobs[future],status='failed',error=str(error));failures.append(event)
            print(json.dumps(event),flush=True)
            write_json(args.output/'status.json',dict(completed=len(results),failed=len(failures),total=len(requested),training_total=30,failures=failures,validation_opened=False))
    write_json(args.output/'receipt.json',dict(version=VERSION,status='failed' if failures else 'complete',sessions_sha256=file_hash(args.sessions),
        completed=sorted(r['day'] for r in results),requested=[item['day'] for item in requested],training_total=30,failures=failures,validation_opened=False,ready_for_replay=False))
    return int(bool(failures))


if __name__=='__main__':raise SystemExit(main())
