"""Opening-known V6 reference contract feeding the unchanged causal V7 fitter."""
from concurrent.futures import ProcessPoolExecutor,wait,FIRST_COMPLETED
from datetime import date
from hashlib import sha256
from pathlib import Path
from time import perf_counter
import os
import numpy as np
import polars as pl
from .runtime import require_runtime
from .structural import FIELDS,PreparedStructure,worker_budget,algorithm_hash,_initialize_worker,_signature,_cache_key,_claim,_load,_compute

def prepare_offline_structure(reader,market,watch,bars,asks,clocks,directory,source_key,*,workers=0,progress=print):
    from research.rl_trading.v6.reference import read_reference
    from research.rl_trading.v1.common import digest
    day=market.sessions[0];session=date.fromisoformat(day);tickers=tuple(watch['ticker'].to_list())
    if market.sessions != (day,) or not tickers or len(set(tickers)) != len(tickers):
        raise ValueError('Offline structural inputs require one session and unique listings')
    if asks.shape != (len(clocks),len(tickers)) or not len(clocks):
        raise ValueError('Offline structural asks require [seconds, listing] lanes')
    width=min(worker_budget(workers),len(tickers));directory=require_runtime(directory/'offline-structural-cache')
    algorithm=sha256((algorithm_hash()+sha256(Path(__file__).read_bytes()).hexdigest()).encode()).hexdigest()
    targets=np.full((len(clocks),len(tickers),15),np.inf,dtype=np.float64);valid=np.zeros((len(clocks),len(tickers)),dtype=bool)
    selected=bars.filter((pl.col('price_valid_1000')==1)&(pl.col('extremes_valid_1000')==1))
    groups=selected.select('ticker',*FIELDS).sort('ticker','time_us').partition_by('ticker',as_dict=True)
    pending={};receipts={};completed=missing=0;started=perf_counter()
    def collect(futures):
        nonlocal completed
        for future in futures:
            index,path,signature,claim=pending.pop(future)
            try:
                future.result();target,clock,receipt=_load(path,signature,len(clocks))
                targets[:,index]=target;valid[:,index]=clock;receipts[tickers[index]]=receipt;completed+=1
            finally:claim.__exit__(None,None,None)
        progress(dict(stage='Stream opening-known V7',completed=completed,total=len(tickers)))
    # Spawned Windows workers import numerical libraries before initialization.
    # Limit their import-time thread pools as well as the runtime pools.
    for name in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS'):
        os.environ[name]='1'
    with ProcessPoolExecutor(max_workers=width,initializer=_initialize_worker) as pool:
        try:
            for index,listing in enumerate(watch.iter_rows(named=True)):
                seed,splits,_,evidence=read_reference(reader,session,listing)
                if seed is None:
                    # This is the V6 certified missing-reference state. It
                    # masks structural targets, never manufactures a seed.
                    receipts[listing['ticker']]=dict(status='v6-missing-prior-v7-masked',reference_hash=evidence['hash'])
                    missing+=1;completed+=1;continue
                frame=groups.get((listing['ticker'],),pl.DataFrame(schema={f:pl.Float64 for f in FIELDS}))
                rows={f:np.ascontiguousarray(frame[f].to_numpy()) for f in FIELDS};ask=np.ascontiguousarray(asks[:,index])
                signature=_signature(listing['ticker'],day,seed,splits,rows,ask,clocks,source_key,algorithm)
                path=require_runtime(directory/_cache_key(signature));claim=_claim(path);claim.__enter__()
                future=pool.submit(_compute,listing['ticker'],day,seed,splits,rows,ask,clocks,str(path),signature)
                pending[future]=(index,path,signature,claim)
                if len(pending)>=2*width:collect(wait(pending,return_when=FIRST_COMPLETED)[0])
            while pending:collect(wait(pending,return_when=FIRST_COMPLETED)[0])
        finally:
            # Children finish within the executor lifetime; release every
            # parent claim even when one worker fails.
            for future,(_,_,_,claim) in pending.items():
                future.cancel();claim.__exit__(None,None,None)
    stable={ticker:{key:value for key,value in receipt.items() if key!='preparation_seconds'}
            for ticker,receipt in receipts.items()}
    token=digest(dict(version='v4-offline-structure-certificate-v1',algorithm=algorithm,
                      source_key=source_key,receipts=stable))
    return PreparedStructure(targets,valid,token,dict(workers=width,completed=completed,missing_prior_v7=missing,seconds=perf_counter()-started,algorithm=algorithm))
