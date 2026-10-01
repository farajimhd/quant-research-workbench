"""Sparse pinned ARTE broker chunks; no features or dense quote grids saved.

Project each required source field once from certified broker_100ms units.
Persist sparse Parquet for repeated PPO collection, materialize bounded [T,N]
execution tensors only transiently. LULD stays in its existing sparse sidecar.
"""
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import numpy as np
import polars as pl
import torch
from research.rl_trading.v1 import arte_sql as sql
from research.rl_trading.v1.common import digest,file_hash,exclusive
from research.rl_trading.v6.tensor_broker import BrokerBucket

VERSION='rl-v6-sparse-broker-100ms-2'
SCHEMA={'ticker':pl.String,'bucket_index':pl.Int64,'execution_volume':pl.Float64,
    'execution_notional':pl.Float64,'volume_valid':pl.Int64,'high_int':pl.Int64,
    'low_int':pl.Int64,'extremes_valid':pl.Int64,'quote_timestamp_us':pl.Int64,
    'bid_int':pl.Int64,'ask_int':pl.Int64,'quote_valid':pl.Int64}


class BrokerShards:
    def __init__(self,source,tickers,root,*,runtime_root):
        self.source=source;self.tickers=tuple(tickers);self.root=Path(root).resolve()
        runtime=Path(runtime_root).resolve()
        if not runtime.is_dir() or not self.root.is_relative_to(runtime):
            raise ValueError('Broker shards require the configured runtime')
        if len(set(tickers))!=len(tickers) or set(tickers)-set(source.attempts):
            raise ValueError('Broker shard identity lacks a certified source unit')
        self.coverage=None;coverage_hash=None
        if getattr(source,'expose_execution_features',False):
            from research.rl_trading.v6.sparse_coverage import certify_source
            self.coverage,coverage_hash=certify_source(source,tickers)
        self.root.mkdir(parents=True,exist_ok=True)
        contract={'version':VERSION,'day':str(source.day),'build_id':source.source['build_id'],
            'definition_hash':source.source['definition_hash'],'fields':list(SCHEMA),
            'sparse_coverage_sha256':coverage_hash,
            'listing_hash':digest(tickers),'attempt_hash':digest([(t,source.attempts[t]) for t in tickers])}
        manifest=self.root/'manifest.json'
        if manifest.exists() and json.loads(manifest.read_text())!=contract:
            raise ValueError('Broker shard namespace changed certified identity')
        if not manifest.exists():manifest.write_text(json.dumps(contract,sort_keys=True))
        self.contract=contract
        source.broker_shard_contract=VERSION
        source.broker_shard_scope='projected_sparse_certified_population_bounded_chunks'
        source.broker_shard_certificates={}

    def read(self,start,end):
        with exclusive(self.root/f'{start}-{end}.lock'):
            return self._read_locked(start,end)

    def _read_locked(self,start,end):
        s=self.source
        if not s.origin<=start<end<=s.end_us or (end-start)%100_000 or (start-s.origin)%100_000:
            raise ValueError('Invalid broker shard clock bounds')
        name=f'{start}-{end}';path=self.root/f'{name}.parquet';cert=self.root/f'{name}.json'
        if cert.exists():
            report=json.loads(cert.read_text())
            if report['contract_hash']!=digest(self.contract) or file_hash(path)!=report['sha256']:
                raise ValueError('Broker shard hash or source contract mismatch')
            s.broker_shard_certificates[name]=file_hash(cert)
            return pl.read_parquet(path)
        first=(start-s.origin)//100_000;last=(end-s.origin)//100_000-1
        parts=[]
        for offset in range(0,len(self.tickers),256):
            group=self.tickers[offset:offset+256]
            scope=','.join(f'({sql.literal(t)},toUUID({sql.literal(s.attempts[t])}))' for t in group)
            parts.append(s._frame('SELECT '+','.join(SCHEMA)+' FROM arte.liquidity_100ms_v1 '
                f'WHERE build_id={sql.literal(s.source["build_id"])} AND session_date=toDate({sql.literal(s.day)}) '
                f'AND resolution_ms=100 AND bucket_index BETWEEN {first} AND {last} AND (ticker,attempt_id) IN ({scope}) '
                'ORDER BY bucket_index,ticker',SCHEMA))
        rows=pl.concat(parts).sort('bucket_index','ticker')
        validate_rows(rows,first,last,set(self.tickers))
        temporary=path.with_suffix('.parquet.tmp');rows.write_parquet(temporary,compression='zstd');temporary.replace(path)
        report={'status':'complete','contract_hash':digest(self.contract),'rows':rows.height,
            'start_us':start,'end_us':end,'sha256':file_hash(path),
            'empty_bucket_semantics':('certified_no_event_zero_volume' if self.coverage is not None else 'missing_evidence_not_zero_capacity')}
        temp=cert.with_suffix('.json.tmp');temp.write_text(json.dumps(report,sort_keys=True));temp.replace(cert)
        s.broker_shard_certificates[name]=file_hash(cert)
        return rows

    def buckets(self,start,end,*,device,luld,chunk_seconds=15,max_device_bytes=1<<30):
        if chunk_seconds<1:raise ValueError('Positive broker chunk bound required')
        windows=iter((begin,min(end,begin+chunk_seconds*1_000_000))
            for begin in range(start,end,chunk_seconds*1_000_000))
        current=next(windows,None)
        if current is None:return
        # One I/O worker, one queued chunk: overlap source/cache reads with
        # current GPU execution without concurrent access to the HTTP reader.
        with ThreadPoolExecutor(max_workers=1,thread_name_prefix='v6-broker-read') as worker:
            pending=worker.submit(self.read,*current)
            while current is not None:
                rows=pending.result();following=next(windows,None)
                if following is not None:pending=worker.submit(self.read,*following)
                yield from materialize(rows,self.tickers,self.source.origin,*current,
                    device=device,luld=luld,max_device_bytes=max_device_bytes,coverage=self.coverage)
                current=following


def validate_rows(rows,first,last,tickers):
    if rows.height and (rows.select('ticker','bucket_index').unique().height!=rows.height or
            not set(rows['ticker'].unique())<=tickers or
            rows['bucket_index'].min()<first or rows['bucket_index'].max()>last):
        raise ValueError('Duplicate or out-of-scope broker evidence')
    for name in ('execution_volume','execution_notional'):
        if rows[name].null_count() or not np.isfinite(rows[name].to_numpy()).all() or (rows[name]<0).any():
            raise ValueError('Invalid eligible broker volume/notional')
    for name in ('volume_valid','extremes_valid','quote_valid'):
        if not rows[name].is_in([0,1]).all():raise ValueError('Invalid broker validity mask')
    bad=rows.filter((pl.col('extremes_valid')==1)&
        ((pl.col('low_int')<=0)|(pl.col('high_int')<pl.col('low_int'))))
    if bad.height:raise ValueError('Malformed broker extrema')


def materialize(rows,tickers,origin,start,end,*,device,luld,max_device_bytes=1<<30,coverage=None):
    """Sparse rows -> bounded [T,N] GPU tensors; never persisted dense.

    Unknown prior-close LULD coverage remains explicit in the original source
    certificate. No new official halt claim or synthesized halt table is made.
    """
    t=(end-start)//100_000;n=len(tickers)
    # Raw source columns plus derived arrays/masks; guard a conservative peak.
    if t*n*128>max_device_bytes:raise MemoryError('Broker chunk exceeds GPU materialization budget')
    clocks=np.arange(start+100_000,end+1,100_000,dtype=np.int64)
    by=pl.DataFrame({'ticker':tickers,'listing':np.arange(n,dtype=np.int64)})
    mapped=rows.join(by,on='ticker',how='left')
    if mapped['listing'].null_count():raise ValueError('Unmapped broker identity')
    time=(mapped['bucket_index'].to_numpy()-(start-origin)//100_000).astype(np.int64)
    listing=mapped['listing'].to_numpy()
    transfer=lambda a:torch.from_numpy(a).to(device)
    def column(name,dtype=np.float64):
        a=np.zeros((t,n),dtype=dtype);a[time,listing]=mapped[name].to_numpy();return transfer(a)
    volume=column('execution_volume');notional=column('execution_notional')
    vwap=torch.where(volume>0,notional/volume.clamp_min(1e-12),0.)
    valid=column('volume_valid',np.bool_)
    high=column('high_int')/10000;low=column('low_int')/10000
    extrema=column('extremes_valid',np.bool_)
    bid=column('bid_int');ask=column('ask_int');quote_time=column('quote_timestamp_us',np.int64)
    clock_tensor=transfer(clocks)[:,None]
    quote=column('quote_valid',np.bool_)&(bid>0)&(ask>=bid)&(clock_tensor>=quote_time)&(clock_tensor-quote_time<=1_000_000)
    spread=(ask-bid).clamp_min(0)/10000
    pause=np.zeros((t,n),dtype=np.bool_)
    lower=np.zeros((t,n));upper=np.full((t,n),np.inf)
    # Metadata lane: one vectorized as-of search per listing/chunk, never one
    # Python lookup per listing per execution bucket.
    for j,ticker in enumerate(tickers):
        item=luld.rows.get(ticker)
        if item is None:continue
        change,frame=item;idx=np.searchsorted(change,clocks,side='right')-1
        known=idx>=0
        if luld.end_us is not None:known &= clocks<luld.end_us
        pos=idx[known]
        pause[known,j]=frame['paused'].to_numpy()[pos]
        lower[known,j]=frame['lower'].to_numpy()[pos];upper[known,j]=frame['upper'].to_numpy()[pos]
    paused=transfer(pause);band_low=transfer(lower);band_high=transfer(upper)
    # Quote/spread price estimates must also be executable inside modeled bands.
    valid &= (vwap+spread*.5<=band_high)&(vwap-spread*.5>=band_low)
    covered=None
    if coverage is not None:
        if set(tickers)-set(coverage):raise ValueError('Broker listing lacks verified sparse coverage')
        begins=transfer(np.array([coverage[t][0] for t in tickers],np.int64))[None,:]
        ends=transfer(np.array([coverage[t][1] for t in tickers],np.int64))[None,:]
        covered=(clock_tensor-100000>=begins)&(clock_tensor<=ends)
    for i,clock in enumerate(clocks):
        yield BrokerBucket(int(clock),vwap[i],volume[i],high[i],low[i],spread[i],
            valid[i],extrema[i],quote[i],paused[i],band_low[i],band_high[i],bid[i]/10000,ask[i]/10000,quote_time[i],covered[i] if covered is not None else None)
