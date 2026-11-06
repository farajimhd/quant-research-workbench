"""Bounded real-shard qualification; never starts or changes a campaign."""
from research.vectorized_backtest.v67.receipt_storage import read as read_receipt
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
os.environ.setdefault('NUMBA_NUM_THREADS','4')
import argparse,json,queue,time
from pathlib import Path
import numpy as np


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--source-job',required=True);p.add_argument('--output',required=True)
    p.add_argument('--count',type=int,default=1024);p.add_argument('--days',nargs='+',default=['2026-08-13'])
    p.add_argument('--population',type=Path)
    p.add_argument('--reference-kernel',action='store_true',help='Corrected original GPU layout for bounded comparisons only')
    args=p.parse_args()
    from .runtime import configure_caches,require_runtime
    root=require_runtime(args.output);configure_caches(root/'compiler')
    import torch
    if args.reference_kernel:
        from . import network_cuda
        original=network_cuda.market_batch
        def reference_kernel(plan,*values):
            plan.cuda_time_tiled=False
            return original(plan,*values)
        network_cuda.market_batch=reference_kernel
    from .full_common import config,aggregate
    from .session_lanes import Worker
    from .genome import sample,Individual
    from .identity import digest,source_hash
    from .publication import write_json
    from .orders import VERSION
    job=json.loads(Path(args.source_job).read_bytes())
    job['execution']['order_mode']=VERSION
    cfg=config(job,'workstation')
    cfg.update(output=str(root/'replays'),source_sha256=source_hash(),compile_cache_root=str(root/'compiler'),
               gpu_execution_gate=None,stop_roots=[],boundary_stop=str(root),reuse_results=False,
               immutable_population_cache=False,indexed_results=True,host_prefetch=False,host_reserve_gib=0.,
               host_admission_gib=0.,lane_memory_bytes=int(torch.cuda.mem_get_info()[0]*.80))
    members=([Individual.restore(v) for v in json.loads(args.population.read_bytes())] if args.population else
             sample(np.random.default_rng(2021),args.count,stratified=True,structural=True))
    if len(members)!=args.count:raise ValueError('Benchmark population count differs')
    payload=[m.payload() for m in members]
    write_json(root/'population.json',payload)
    worker=Worker(cfg,queue.Queue(),0);reports=[]
    try:
        for day in args.days:
            if day not in job['training_days']:raise ValueError('Qualification day must be training')
            print('Replay',day,'candidates',args.count,flush=True)
            manifest=json.loads((Path(job['dataset'])/'training.json').read_bytes())
            width=len(next(x['selected'] for x in manifest['sessions'] if x['day']==day))
            started=time.perf_counter();result=worker.run(day,None,payload,1,digest(payload),width)
            metrics=read_receipt((root/'replays/generation-0001'/day/'receipt.json'))['metrics']
            panel=aggregate([metrics])
            row=dict(day=day,candidates=args.count,wall_seconds=time.perf_counter()-started,timings=result['timings'],
                     traded=int(np.count_nonzero(panel['closed_positions'])),profitable=int(np.count_nonzero(panel['net_pnl']>0)),
                     median_net=float(np.median(panel['net_pnl'])),best_score=float(np.max(panel['score'])),
                     blocked_requests=int(np.sum(metrics['blocked_requests'])))
            reports.append(row);write_json(root/'report.json',dict(source_sha256=source_hash(),reference_kernel=args.reference_kernel,sessions=reports))
            print(json.dumps(row),flush=True)
    finally:worker.close()


if __name__=='__main__':main()
