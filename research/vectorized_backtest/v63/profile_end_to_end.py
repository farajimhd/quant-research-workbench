"""Bounded real-shard qualification; never starts or changes a campaign."""
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
    args=p.parse_args()
    from .runtime import configure_caches,require_runtime
    root=require_runtime(args.output);configure_caches(root/'compiler')
    import torch
    from .full_common import config,aggregate
    from .session_lanes import Worker
    from .genome import sample
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
    members=sample(np.random.default_rng(2021),args.count,stratified=True,structural=True)
    payload=[m.payload() for m in members]
    worker=Worker(cfg,queue.Queue(),0);reports=[]
    try:
        for day in args.days:
            if day not in job['training_days']:raise ValueError('Qualification day must be training')
            print('Replay',day,'candidates',args.count,flush=True)
            width=len(json.loads((Path(cfg['history'])/day/'complete.json').read_bytes())['listing_ids'])
            started=time.perf_counter();result=worker.run(day,None,payload,1,digest(payload),width)
            metrics=json.loads((root/'replays/generation-0001'/day/'receipt.json').read_bytes())['metrics']
            panel=aggregate([metrics])
            row=dict(day=day,candidates=args.count,wall_seconds=time.perf_counter()-started,timings=result['timings'],
                     traded=int(np.count_nonzero(panel['closed_positions'])),profitable=int(np.count_nonzero(panel['net_pnl']>0)),
                     median_net=float(np.median(panel['net_pnl'])),best_score=float(np.max(panel['score'])),
                     blocked_requests=int(np.sum(panel['blocked_requests'])))
            reports.append(row);write_json(root/'report.json',dict(source_sha256=source_hash(),sessions=reports))
            print(json.dumps(row),flush=True)
    finally:worker.close()


if __name__=='__main__':main()
