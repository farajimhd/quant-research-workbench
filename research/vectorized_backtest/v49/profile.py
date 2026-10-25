"""Bounded composed-graph real Aug13 GPU replay qualification."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
for key in ('OMP_NUM_THREADS','MKL_NUM_THREADS','OPENBLAS_NUM_THREADS'):
    os.environ.setdefault(key,'1')
from research.vectorized_backtest.v42.windows_process import quiet_compilers
quiet_compilers()
import argparse
import gc
import hashlib
import json
from pathlib import Path
from time import perf_counter
import numpy as np
import torch
from .data import SessionData
from .evaluator import Execution,PopulationPrograms,replay_cohort
from .identity import digest,source_hash
from research.vectorized_backtest.v42.io_pipeline import prepare_host,TransferStaging
from .genome import Individual
from research.vectorized_backtest.v42.runtime import configure_caches
from research.vectorized_backtest.v42.publication import write_json
from research.vectorized_backtest.v6.torch_backtest.runtime import require_runtime,file_hash


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job',type=Path,required=True)
    parser.add_argument('--pool',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--candidates',type=int,default=64)
    parser.add_argument('--resume-variants',action='store_true',help='Reuse an already completed unchanged close-price control')
    args=parser.parse_args()
    if not 1<=args.candidates<=1024:raise ValueError('One bounded representative cohort required')
    out=require_runtime(args.output)
    configure_caches(require_runtime(out/'compiler-cache'),recompile_limit=128)
    torch.set_num_threads(1)
    job=json.loads(args.job.read_bytes());day='2026-08-13'
    from research.vectorized_backtest.v42.run_search import source_hash as original_hash
    if original_hash()!=job['source_sha256'] or file_hash(job['training'])!=job['training_sha256']:
        raise ValueError('Pinned original job/training changed')
    cp=json.loads((Path(job['campaign'])/'local'/day/'checkpoint.json').read_bytes())
    if digest(cp['state'])!=cp['state_sha256']:raise ValueError('Original sealed checkpoint changed')
    prior=cp['contract'];dataset=prior['dataset'];manifest=json.loads(Path(job['training']).read_bytes())
    pool=json.loads(args.pool.read_bytes())
    if digest(pool['population'])!=pool['population_sha256']:raise ValueError('Original cohort changed')
    sealpath=Path(job['campaign'])/'local'/day/'generation-0001/complete.json'
    if file_hash(sealpath)!=cp['state']['seals']['1']:raise ValueError('Initial cohort seal changed')
    if pool['population_sha256']!=json.loads(sealpath.read_bytes())['population_sha256']:
        raise ValueError('Profile cohort not from the original initial sample')
    indexes=np.linspace(0,len(pool['population'])-1,args.candidates,dtype=int)
    members=[Individual.restore(pool['population'][int(i)]) for i in indexes]
    started=perf_counter();print('Load one certified training session, once',flush=True)
    data=SessionData(Path(dataset['inputs'])/day,Path(dataset['history'])/day,
        device='cpu',maximum_gib=4.,feature_cache=Path(manifest['feature_cache'])/day if manifest.get('feature_cache') else None,
        indicator_root=Path(dataset['indicator_root'])/day,enriched_root=Path(dataset['enriched_root'])/day)
    data=prepare_host(data,resident=True)
    load_seconds=perf_counter()-started
    staging=TransferStaging();upload=perf_counter();staging.activate([(day,data)],'cuda');torch.cuda.synchronize()
    upload_seconds=perf_counter()-upload
    print('Resident data ready; matched cohort',args.candidates,'GPU free GiB',torch.cuda.mem_get_info()[0]/2**30,flush=True)

    from .whole_network import cross
    composed=[];pairs=[]
    for i in range(0,len(members)-1,2):
        try:child,meta=cross(members[i],members[i+1],('union','intersection','xor')[(i//2)%3])
        except ValueError:continue
        composed.append(child);pairs.append([int(indexes[i]),int(indexes[i+1])])
    if not composed:raise ValueError('No bounded composed graphs in profile sample')
    from dataclasses import replace
    from .ownership import audit
    case_map=dict(day=day,listing_ids=[data.receipt['listings'][int(j)]['listing_id'] for j in data.listing_ids],security_ids=[data.receipt['listings'][int(j)]['security_id'] for j in data.listing_ids])
    cases=sorted({day+':'+x for x in case_map['security_ids']})
    assert all(m.case_owner is None for m in composed)
    data.clocks=min(data.clocks,1025)
    members=composed
    results={};financial={}
    try:
        for name,enabled in (('session','0'),):
            os.environ['V49_OWNER_LANES']=enabled
            programs=PopulationPrograms(members,'cuda');plan=programs.evaluate(data)
            try:
                capture=perf_counter()
                prepared=replay_cohort([data],members,[plan],execution=Execution(**prior['execution']),
                    backend='compile',episode_capacity=65536,trace=True,listing_width=len(data.listing_ids),capture_only=True)
                capture_seconds=perf_counter()-capture
                prepared.replay.reset();torch.cuda.synchronize()
                begin=torch.cuda.Event(enable_timing=True);end=torch.cuda.Event(enable_timing=True)
                begin.record();prepared.replay.enqueue();end.record();torch.cuda.synchronize()
                gpu_seconds=begin.elapsed_time(end)/1000
                raw=prepared.export()[0]
                metrics={k:v.detach().cpu().tolist() if isinstance(v,torch.Tensor) else v for k,v in raw.items()}
                from .financial import ticker_totals
                metrics['ticker_pnl']=ticker_totals(metrics,data)
                audit(members,metrics,case_map)
                if not torch.isfinite(raw['net_pnl']).all():raise ValueError('Nonfinite composed replay')
                financial[name]=digest(metrics)
                results[name]=dict(capture_seconds=capture_seconds,gpu_replay_seconds=gpu_seconds,financial_sha256=financial[name])
                print(name,results[name],flush=True)
            finally:
                programs.close()
                del prepared,plan,programs
                gc.collect();torch.cuda.empty_cache()
        result=dict(source_sha256=source_hash(),composed_candidates=len(members),
            clocks=data.clocks-1,listing_width=len(data.listing_ids),load_seconds=load_seconds,
            upload_seconds=upload_seconds,gpu_replay_seconds=results['session']['gpu_replay_seconds'],
            variants=results,selection_scope='complete-session; unrestricted tickers',
            cohort_sha256=digest([m.payload() for m in members]),
            original_initial_population_sha256=pool['population_sha256'],
            finite_financial_results=True,ownership_verified=True,validation_opened=False,
            peak_allocated_bytes=torch.cuda.max_memory_allocated())
        write_json(out/'profile.json',result);print(json.dumps(result),flush=True)
    finally:
        os.environ.pop('V49_OWNER_LANES',None)
        staging.close();data.close()


if __name__=='__main__':main()
