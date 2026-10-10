"""One session worker, bounded ticker workers; certified compact inputs only."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
os.environ.setdefault('OMP_NUM_THREADS','1')
os.environ.setdefault('POLARS_MAX_THREADS','1')
import argparse,json
from concurrent.futures import ProcessPoolExecutor,wait,FIRST_COMPLETED
from pathlib import Path
from time import perf_counter
import numpy as np
import polars as pl
from .history_bank import VERSION,SWING_WINDOWS,LAYOUT,calculate,compress
from .runtime import require_runtime,write_json,file_hash
from .sparse_replay import verify_sparse_receipt
from .materialize import owned_run


def ticker_job(payload):
    listing,clocks,fields,folder=payload
    folder=require_runtime(folder);n=len(clocks)
    at=fields['clock']-clocks[0]
    if np.any(np.diff(at)<=0):raise ValueError('Ticker clocks invalid')
    data={k:np.full(n,False if k=='observed' else 0. if k=='notional' else np.nan,
        dtype=bool if k=='observed' else np.float64) for k in ('mark','high','low','observed','notional')}
    inside=(at>=0)&(at<n)
    for name,array in data.items():array[at[inside]]=fields[name][inside]
    values,ids=compress(calculate(**data))
    source=np.full(n,-1,dtype=np.int64)
    locations=np.searchsorted(fields['clock'],clocks,side='right')-1
    known=locations>=0
    source[known]=fields['source_row'][locations[known]]
    for name,array in (('values',values),('ids',ids),('source_rows',source)):
        np.save(folder/(name+'.npy'),array,allow_pickle=False)
    record=dict(listing=listing,rows=len(values),clocks=n,files={name+'.npy':file_hash(folder/(name+'.npy'))
        for name in ('values','ids','source_rows')})
    write_json(folder/'complete.json',record);return record


def physical_source_rows(frame):
    if 'source_row' in frame.columns:
        if not np.array_equal(frame['source_row'].to_numpy(),np.arange(len(frame))):
            raise ValueError('Certified market source_row does not match physical row order')
        return frame
    return frame.with_row_index('source_row')


def prepare(inputs,output,workers=8):
    inputs=Path(inputs);output=require_runtime(output);start=perf_counter()
    with owned_run(output,version=VERSION):
        receipt=verify_sparse_receipt(inputs)
        clocks=np.load(inputs/'clocks.npy',allow_pickle=False)
        if not len(clocks) or np.any(np.diff(clocks)!=1):raise ValueError('Require contiguous elapsed-second clocks')
        top=np.load(inputs/'top_indices.npy',allow_pickle=False)
        union=np.unique(top);union=union[union>=0]
        frame=physical_source_rows(pl.read_parquet(inputs/'market.parquet')).filter(pl.col('listing').is_in(union))
        pending={};records={};cursor=0
        groups=frame.partition_by('listing',as_dict=True,maintain_order=True)
        def submit(pool):
            nonlocal cursor
            listing=int(union[cursor]);cursor+=1
            part=groups.get((listing,))
            if part is None:raise ValueError('Ranked ticker has no certified market rows')
            part=part.sort('clock')
            fields={k:part[k].fill_null(False if k=='observed' else 0. if k=='notional' else float('nan')).to_numpy()
                    for k in ('clock','mark','high','low','observed','notional','source_row')}
            future=pool.submit(ticker_job,(listing,clocks,fields,output/'tickers'/str(listing)))
            pending[future]=listing
        def publish(stage):
            write_json(output/'status.json',dict(stage=stage,completed=len(records),total=len(union),active=len(pending),
                queued=len(union)-cursor,elapsed_seconds=perf_counter()-start,validation_opened=False))
        publish('Verify inputs and partition ranked ticker union')
        with ProcessPoolExecutor(max_workers=workers) as pool:
            for _ in range(min(workers,len(union))):submit(pool)
            while pending:
                for future in wait(pending,return_when=FIRST_COMPLETED)[0]:
                    listing=pending.pop(future);records[listing]=future.result()
                    if cursor<len(union):submit(pool)
                publish('Calculate and persist causal ticker histories')
        publish('Merge compressed histories and lookup indices')
        rows=sum(v['rows'] for v in records.values())
        required=rows*332*8+len(clocks)*len(union)*12
        import shutil
        if required>shutil.disk_usage(output).free*.8:raise MemoryError('History output exceeds free disk headroom')
        values=np.lib.format.open_memmap(output/'values.npy',mode='w+',dtype=np.float64,shape=(rows,332))
        ids=np.lib.format.open_memmap(output/'ids.npy',mode='w+',dtype=np.int32,shape=(len(clocks),len(union)))
        source=np.lib.format.open_memmap(output/'source_rows.npy',mode='w+',dtype=np.int64,shape=ids.shape)
        offset=0
        if rows>=2**31:raise MemoryError('Compressed row identity exceeds int32')
        for column,listing in enumerate(union):
            folder=output/'tickers'/str(listing);record=records[int(listing)]
            for name,digest in record['files'].items():
                if file_hash(folder/name)!=digest:raise ValueError('Ticker history bytes changed')
            count=record['rows'];values[offset:offset+count]=np.load(folder/'values.npy',mmap_mode='r')
            ids[:,column]=np.load(folder/'ids.npy',mmap_mode='r')+offset
            source[:,column]=np.load(folder/'source_rows.npy',mmap_mode='r');offset+=count
        values.flush();ids.flush();source.flush()
        record=dict(status='complete',version=VERSION,day=receipt['identity']['session']['day'],listing_ids=union.tolist(),
            clocks=len(clocks),compressed_rows=rows,uncompressed_rows=len(clocks)*len(union),resident_bytes=required,
            input_receipt_sha256=file_hash(inputs/'complete.json'),implementation_sha256=file_hash(Path(__file__).with_name('history_bank.py')),
            swing_windows=list(SWING_WINDOWS),layout=LAYOUT,elapsed_seconds=perf_counter()-start,
            files={name+'.npy':file_hash(output/(name+'.npy')) for name in ('values','ids','source_rows')},
            validation_opened=False,scope='ranked union; every session clock including after rank exit')
        write_json(output/'complete.json',record);publish('Complete and hash sealed')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--inputs',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--workers',type=int,default=8)
    a=p.parse_args()
    if not 1<=a.workers<=8:p.error('workers must be 1..8')
    prepare(a.inputs,a.output,a.workers)

if __name__=='__main__':main()
