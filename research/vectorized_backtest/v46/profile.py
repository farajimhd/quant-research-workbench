"""Matched resident Aug13 replay: original close fills, bid/ask, optimized bid/ask."""
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
from research.vectorized_backtest.v42.genome import Individual
from research.vectorized_backtest.v42.runtime import configure_caches
from research.vectorized_backtest.v42.publication import write_json
from research.vectorized_backtest.v6.torch_backtest.runtime import require_runtime,file_hash


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job',type=Path,required=True)
    parser.add_argument('--pool',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--candidates',type=int,default=1024)
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
    from research.vectorized_backtest.v42.evaluator import replay_cohort as original_replay,Execution as OriginalExecution
    records=[];variant_hash=None
    try:
        for name,masked in [('close',None),('bid-ask-naive','0'),('bid-ask-masked','1')]:
            if name=='close' and args.resume_variants:
                r=json.loads((out/'close.json').read_bytes())
                if (r['candidates'],r['clocks'],r['listing_width'])!=(args.candidates,data.clocks-1,len(data.listing_ids)):raise ValueError('Profile control shape changed')
                records.append(r);print('Reuse completed close-price control',flush=True);continue
            os.environ['V46_MASK_QUOTES']=masked or '0'
            print('Capture',name,flush=True)
            programs=PopulationPrograms(members,'cuda');plan=programs.evaluate(data)
            execution=OriginalExecution(**prior['execution']) if name=='close' else Execution(**prior['execution'])
            run=original_replay if name=='close' else replay_cohort
            captured_at=perf_counter()
            prepared=run([data],members,[plan],execution=execution,backend='compile',episode_capacity=65536,
                         trace=True,listing_width=len(data.listing_ids),capture_only=True)
            capture_seconds=perf_counter()-captured_at
            prepared.replay.reset();torch.cuda.synchronize()
            begin=torch.cuda.Event(enable_timing=True);end=torch.cuda.Event(enable_timing=True)
            wall=perf_counter();begin.record();prepared.replay.enqueue();end.record();torch.cuda.synchronize()
            replay_seconds=perf_counter()-wall;gpu_seconds=begin.elapsed_time(end)/1000
            export_at=perf_counter();raw=prepared.export()[0]
            result={k:v.detach().cpu().tolist() if isinstance(v,torch.Tensor) else v for k,v in raw.items()}
            export_seconds=perf_counter()-export_at
            # Compare all common financial/episode outputs exactly; V46 adds a diagnostic count.
            financial={k:v for k,v in result.items() if k!='unliquidated_positions'}
            token=hashlib.sha256(json.dumps(financial,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
            if name=='bid-ask-naive':variant_hash=token
            if name=='bid-ask-masked' and token!=variant_hash:
                raise ValueError('Quote-load optimization changed replay results')
            r=dict(mode=name,candidates=args.candidates,clocks=data.clocks-1,listing_width=len(data.listing_ids),
                   capture_seconds=capture_seconds,steady_replay_seconds=replay_seconds,gpu_replay_seconds=gpu_seconds,
                   export_seconds=export_seconds,result_sha256=token,net_profit_sum=sum(result['net_pnl']),
                   feasible=sum(result['terminal_valid']),positions=sum(result['closed_positions']),
                   peak_allocated_bytes=torch.cuda.max_memory_allocated())
            records.append(r);write_json(out/(name+'.json'),r);print(json.dumps(r),flush=True)
            del prepared,raw,result,financial,plan;programs.close();del programs
            gc.collect();torch.cuda.empty_cache();torch.cuda.reset_peak_memory_stats()
        result=dict(source_sha256=source_hash(),cohort_sha256=digest([m.payload() for m in members]),
                    original_initial_population_sha256=pool['population_sha256'],indexes=indexes.tolist(),
                    load_seconds=load_seconds,upload_seconds=upload_seconds,reused_completed_close_profile=args.resume_variants,quote_coverage=data.quote_coverage,
                    records=records,optimized_exact_financial_parity=True,validation_opened=False,
                    steady_masked_vs_close=records[2]['gpu_replay_seconds']/records[0]['gpu_replay_seconds'],
                    steady_masked_vs_naive=records[2]['gpu_replay_seconds']/records[1]['gpu_replay_seconds'])
        write_json(out/'profile.json',result)
    finally:
        staging.close();data.close()


if __name__=='__main__':main()
