"""Certified raw V7 targets at sparse completed observations, outside replay."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json
from types import SimpleNamespace
from concurrent.futures import ProcessPoolExecutor,wait,FIRST_COMPLETED
from datetime import date,datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import numpy as np
import polars as pl
from .availability import configure_reader
from .runtime import require_runtime,file_hash,write_json
from .materialize import scope,owned_run
from .sparse_replay import verify_sparse_receipt,KEY_STRIDE
from .structural import FIELDS,algorithm_hash,_initialize_worker,_signature,_cache_key,_claim,_compute,_load,worker_budget
from .reference_prefetch import ordered_references
from .source import arte_source


def reference_transport():
    """Existing V5 reference principal at the certified market endpoint.

    Metadata credentials and market transport discovery are separate concerns.
    Both SQL approval and server readonly=1 remain enforced, without fallback
    credentials or granting privileges to the market-data principal.
    """
    from research.rl_trading.v1.arte_source import reader as reference_reader
    from src.backend.backtest_market_data import readonly_clickhouse_client
    from research.mlops.clickhouse import ClickHouseHttpClient
    reference=reference_reader(threads=1);transport=readonly_clickhouse_client(v3_read_principal=True)
    previous=reference._client
    try:
        reference._client=ClickHouseHttpClient(transport.base_url,previous.user,previous.password,
            timeout_seconds=180,persistent=True,default_query_params=dict(readonly=1,max_threads=1,max_execution_time=150,
                max_memory_usage=2147483648,max_result_rows=700000,max_result_bytes=100000000,result_overflow_mode='throw'))
    finally:previous.close();transport.close()
    return reference


def prepare(inputs_root,output,*,workers=2,reuse_cache=None,reuse_code=None,reuse_version='v4'):
    """Only daily-union identities can enter or become held; retain all their bars."""
    from research.rl_trading.v6.reference import read_reference
    root=Path(inputs_root)
    inputs=SimpleNamespace(root=root,receipt=verify_sparse_receipt(root),arrays={
        name:np.load(root/(name+'.npy'),mmap_mode='r',allow_pickle=False) for name in ('clocks','top_indices','market_keys')})
    output=require_runtime(output)
    if (output/'complete.json').exists():raise ValueError('Structural sidecar already complete; do not overwrite')
    item=inputs.receipt['identity']['session'];day=item['day'];members=inputs.receipt['listings']
    union=np.unique(inputs.arrays['top_indices']);union=union[union>=0].tolist()
    configure_reader(Path(__file__).resolve().parents[4])
    source=arte_source.load_build(item['source_manifest'],item['source_ledger'],[day])
    market=pl.read_parquet(inputs.root/'market.parquet')
    if not np.array_equal(market['source_row'].to_numpy(),np.arange(market.height)) or not np.array_equal(
        market['listing'].to_numpy().astype(np.int64)*KEY_STRIDE+market['clock'].to_numpy(),inputs.arrays['market_keys']):
        raise ValueError('Structural source-row identity disagrees with certified sparse keys')
    # Partition once instead of rescanning every market row for each ticker.
    lanes=market.filter(pl.col('listing').is_in(union)).select(
        'listing','clock','source_row','ask','observed','high','low','mark').partition_by('listing',as_dict=True)
    targets=np.full((market.height,15),np.inf,dtype=np.float64);valid=np.zeros(market.height,dtype=bool)
    origin=int(datetime.fromisoformat(day+'T00:00:00').replace(tzinfo=ZoneInfo('America/New_York')).timestamp())
    clocks=inputs.arrays['clocks'];selected=[members[i] for i in union]
    if (reuse_cache is None)!=(reuse_code is None):raise ValueError('Both immutable legacy code and cache root are required for reuse')
    reuse=None;reused=0;reuse_misses={}
    if reuse_cache is not None:
        from .structural_reuse import DenseCacheReuse
        reuse=DenseCacheReuse(item['execution_root'],reuse_cache,reuse_code,day=day,clocks=clocks,version=reuse_version)
    ids={members[i]['ticker']:i for i in union}
    width=min(worker_budget(workers),len(union));pending={};receipts={};completed=0
    cache=require_runtime(output/'cache');algorithm=algorithm_hash()
    def collect(futures):
        nonlocal completed
        for future in futures:
            ticker,path,signature,claim,indices,stamps=pending.pop(future)
            try:
                future.result();levels,available,receipt=_load(path,signature,len(stamps))
                targets[indices]=levels;valid[indices]=available;receipts[ticker]=receipt;completed+=1
            finally:claim.__exit__(None,None,None)
        write_json(output/'status.json',dict(completed=completed,active=len(pending),total=len(union),validation_opened=False))
        print(dict(completed=completed,total=len(union)),flush=True)
    for name in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS'):os.environ[name]='1'
    reader=arte_source.reader(threads=1)
    storage=arte_source.storage_check(reader)
    # Same reference-reader contract used by V5 offline_structure. The dedicated
    # market principal intentionally lacks float/split metadata table grants.
    references=ordered_references(selected,date.fromisoformat(day),read_reference=read_reference,reader_factory=reference_transport)
    with ProcessPoolExecutor(max_workers=width,initializer=_initialize_worker) as pool:
        try:
            for listing,reference in references:
                ticker=listing['ticker'];seed,splits,_,evidence=reference
                if seed is None:
                    receipts[ticker]=dict(status='v6-missing-prior-v7-masked',reference_hash=evidence['hash']);completed+=1;continue
                arte_source.verify_listing(reader,source,date.fromisoformat(day),ticker)
                statement=(f'SELECT toInt64(bucket_index)+1+{origin} AS clock,open_int,high_int,low_int,close_int,volume '
                    f"FROM arte.bars_v1 WHERE {scope(source,day,[ticker],'bars')} AND resolution_ms=1000 AND price_valid=1 AND extremes_valid=1 "
                    f'AND bucket_index>={int(clocks[0])-origin-1} AND bucket_index<{int(clocks[-1])-origin} ORDER BY bucket_index')
                schema=dict(clock=pl.Int64,open_int=pl.Int64,high_int=pl.Int64,low_int=pl.Int64,close_int=pl.Int64,volume=pl.Float64)
                frame=arte_source.frame(reader,statement,schema)
                arte_source.verify_listing(reader,source,date.fromisoformat(day),ticker)
                lane=lanes[(ids[ticker],)].drop('listing')
                joined=frame.join(lane,on='clock',how='left',validate='1:1')
                if joined['source_row'].null_count() or not joined['observed'].all():raise ValueError('Structural bars have no matching certified compact observation')
                for raw,field in (('close_int','mark'),('high_int','high'),('low_int','low')):
                    if not np.allclose(joined[raw].to_numpy()/10000,joined[field].to_numpy(),rtol=0,atol=1e-8):raise ValueError('Structural raw OHLC disagrees with sparse market')
                if not len(joined):
                    receipts[ticker]=dict(status='no-eligible-structural-observations',reference_hash=evidence['hash']);completed+=1;continue
                stamps=joined['clock'].to_numpy();indices=joined['source_row'].to_numpy()
                rows=dict(zip(FIELDS,[stamps*1000000,*[joined[name].to_numpy() for name in ('open_int','high_int','low_int','close_int','volume')]]))
                asks=joined['ask'].fill_null(float('nan')).to_numpy()
                if reuse is not None:
                    cached,reason=reuse.lookup(ticker,day,seed,splits,rows,asks,stamps)
                    if cached is not None:
                        levels,available,receipt=cached
                        targets[indices]=levels;valid[indices]=available;receipts[ticker]=receipt
                        completed+=1;reused+=1
                        if completed%16==0:
                            write_json(output/'status.json',dict(completed=completed,active=len(pending),total=len(union),reused=reused,validation_opened=False))
                        continue
                    reuse_misses[reason]=reuse_misses.get(reason,0)+1
                signature=_signature(ticker,day,seed,splits,rows,asks,stamps,inputs.receipt['files'],algorithm)
                path=require_runtime(cache/_cache_key(signature));claim=_claim(path);claim.__enter__()
                future=pool.submit(_compute,ticker,day,seed,splits,rows,asks,stamps,str(path),signature)
                pending[future]=(ticker,path,signature,claim,indices,stamps)
                if len(pending)>=2*width:collect(wait(pending,return_when=FIRST_COMPLETED)[0])
            while pending:collect(wait(pending,return_when=FIRST_COMPLETED)[0])
        finally:
            references.close();reader.close()
            for future,(_,_,_,claim,_,_) in pending.items():future.cancel();claim.__exit__(None,None,None)
    np.save(output/'targets.npy',targets,allow_pickle=False);np.save(output/'valid.npy',valid,allow_pickle=False)
    record=dict(version='v6-sparse-structural-v1',status='complete',day=day,input_receipt_sha256=file_hash(inputs.root/'complete.json'),
        market_keys_sha256=inputs.receipt['files']['market_keys.npy'],algorithm_sha256=algorithm,implementation_sha256=file_hash(Path(__file__)),listing_ids=union,
        files={n:file_hash(output/n) for n in ('targets.npy','valid.npy')},receipts=receipts,storage=storage,
        source_units={ticker:source['units'][day][ticker] for ticker in ids},validation_opened=False)
    record['reuse']=dict(enabled=reuse is not None,reused=reused,miss_reasons=reuse_misses,
        evidence=reuse.evidence if reuse is not None else None)
    write_json(output/'complete.json',record);return record


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--inputs',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--workers',type=int,default=2)
    p.add_argument('--reuse-cache',type=Path);p.add_argument('--reuse-code',type=Path)
    p.add_argument('--reuse-version',choices=['v4','v5'],default='v4')
    a=p.parse_args(argv)
    with owned_run(require_runtime(a.output),version='v6-sparse-structural-v1'):
        prepare(a.inputs,a.output,workers=a.workers,reuse_cache=a.reuse_cache,reuse_code=a.reuse_code,reuse_version=a.reuse_version)


if __name__=='__main__':main()
