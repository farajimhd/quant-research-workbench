"""Recover original V32 initial pools on CPU; never run a backtest."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
for key in ('OMP_NUM_THREADS','MKL_NUM_THREADS','OPENBLAS_NUM_THREADS'):os.environ[key]='1'
import argparse,json,time
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor,as_completed
import multiprocessing as mp
from .initial_pool import recover_day,assemble
from .publication import write_json
from .run_search import bind_dataset


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('campaign','training','output'):p.add_argument('--'+key,type=Path,required=True)
    p.add_argument('--seed',type=int,default=2020);p.add_argument('--workers',type=int,default=2)
    args=p.parse_args(argv)
    from research.vectorized_backtest.v6.torch_backtest.runtime import require_runtime
    import psutil
    output=require_runtime(args.output);output.mkdir(parents=True,exist_ok=True)
    if not 1<=args.workers<=4:raise ValueError('At most four bounded recovery workers')
    if psutil.virtual_memory().available<(160+8*args.workers)*2**30:raise MemoryError('Recovery must preserve bars reserve and admission')
    days=bind_dataset(args.training,'training')['days']
    if len(days)!=24:raise ValueError('Original 24 sessions required')
    began=time.perf_counter();pending=[]
    with ProcessPoolExecutor(max_workers=args.workers,mp_context=mp.get_context('spawn')) as executor:
        for i,day in enumerate(days):
            quotas=tuple(total//24+int(i<total%24) for total in (512,256,256))
            path=output/(day+'.json')
            if path.exists():continue
            pending.append(executor.submit(recover_day,args.campaign,args.training,day,quotas,args.seed,output))
        for future in as_completed(pending):
            result=future.result();print(json.dumps(result),flush=True)
            write_json(output/'progress.json',dict(completed=sum((output/(d+'.json')).exists() for d in days),total=24,latest=result))
    records=[json.loads((output/(day+'.json')).read_bytes()) for day in days]
    pool=assemble(records,args.seed)
    from research.vectorized_backtest.v6.torch_backtest.runtime import file_hash
    from .run_search import digest
    pool.pop('pool_sha256')
    pool['recovery_certificates']={d:file_hash(output/(d+'.json')) for d in days}
    pool['pool_sha256']=digest(pool);write_json(output/'seed-pool.json',pool)
    write_json(output/'complete.json',dict(seconds=time.perf_counter()-began,population=1024,categories=pool['categories'],pool_sha256=pool['pool_sha256'],replays=0))


if __name__=='__main__':main()
