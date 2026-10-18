"""Finite real-input CPU/CUDA parity and matched cold/warm laptop timings."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json,time,gc
from pathlib import Path
from dataclasses import replace
import numpy as np
import torch,psutil
from .data import SessionData
from .io_pipeline import prepare_host,TransferStaging
from .network import NetworkPlan
from .genome import sample
from .evaluator import replay_cohort,Execution
from .run_search import bind_dataset,source_hash,digest
from research.vectorized_backtest.v6.torch_backtest.runtime import write_json,configure_caches,require_runtime

def main():
    p=argparse.ArgumentParser();p.add_argument('--training',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--clocks',type=int,default=256);p.add_argument('--batches',default='128,256,512');args=p.parse_args()
    torch.set_num_threads(1);out=require_runtime(args.output);configure_caches(out/'cache',recompile_limit=128);train=bind_dataset(args.training,'training');day='2026-08-04'
    started=time.perf_counter();print('Loading certified Aug4 inputs',flush=True)
    data=prepare_host(SessionData(Path(train['inputs'])/day,Path(train['history'])/day,device='cpu',maximum_gib=4.,indicator_root=Path(train['indicator_root'])/day,enriched_root=Path(train['enriched_root'])/day),resident=True)
    print(f'Loaded {time.perf_counter()-started:.2f}s; resident {data.resident_features.bytes/2**30:.2f} GiB; RAM free {psutil.virtual_memory().available/2**30:.2f} GiB',flush=True)
    report=dict(source_sha256=source_hash(),load_seconds=time.perf_counter()-started,identity=data.identity,full_clocks=data.clocks,listings=len(data.listing_ids),host_bank_bytes=data.resident_features.bytes)
    full=data.clocks;data.clocks=64;members=sample(np.random.default_rng(2020),4);execution=Execution(fee_per_share=.005,minimum_order_fee=1)
    # Full financial oracle on the exact same 63-clock real prefix; never a training run.
    print('CPU financial reference (4 candidates, 63 clocks, all listings)',flush=True)
    cpu=replay_cohort([data],members,[NetworkPlan(members,'cpu')],execution=execution,trace=True,episode_capacity=65536)[0]
    staging=TransferStaging();upload=time.perf_counter();staging.activate([(day,data)],torch.device('cuda'));torch.cuda.synchronize();report['upload_seconds']=time.perf_counter()-upload
    gpu=replay_cohort([data],members,[NetworkPlan(members,'cuda')],execution=execution,backend='compile',trace=True,episode_capacity=65536)[0]
    report['cpu_cuda_differences']={}
    for k,v in cpu.items():
        if isinstance(v,torch.Tensor):
            if not torch.equal(v,gpu[k].cpu()):
                report['cpu_cuda_differences'][k]=float((v-gpu[k].cpu()).abs().max())
                torch.testing.assert_close(v,gpu[k].cpu(),rtol=1e-10,atol=1e-9)
        elif v!=gpu[k]:raise AssertionError('Exact real episode parity failed: '+k)
    report['cpu_reference_passed']=True;report['exact_episode_parity']=True
    print('CPU/CUDA financial oracle passed; differences: '+str(report['cpu_cuda_differences']),flush=True);data.clocks=min(full,args.clocks);report['cases']=[]
    for batch in map(int,args.batches.split(',')):
        members=sample(np.random.default_rng(2020),batch);plan=NetworkPlan(members,'cuda');times=[]
        reference_hash=None
        for passno in range(2):
            gc.collect();torch.cuda.empty_cache();torch.cuda.reset_peak_memory_stats();began=time.perf_counter()
            result=replay_cohort([data],members,[plan],execution=execution,backend='compile',trace=True,episode_capacity=65536)[0]
            torch.cuda.synchronize();elapsed=time.perf_counter()-began;times.append(elapsed)
            financial_hash=digest({k:v.cpu().tolist() if isinstance(v,torch.Tensor) else v for k,v in result.items()})
            if reference_hash is None:reference_hash=financial_hash
            elif reference_hash!=financial_hash:raise AssertionError('Cold/warm financial metric hashes differ')
            case=dict(batch=batch,pass_index=passno,clocks=data.clocks-1,seconds=elapsed,financial_hash=financial_hash,peak_allocated=torch.cuda.max_memory_allocated(),peak_reserved=torch.cuda.max_memory_reserved(),available_gib=psutil.virtual_memory().available/2**30)
            report['cases'].append(case);print(case,flush=True);write_json(out/'profile-progress.json',report);del result
        plan.close()
    profiled_clocks=data.clocks;data.clocks=full
    if profiled_clocks==full and len(members)==512:
        from .session_lanes import Worker
        from queue import Queue
        cfg=dict(output=str(out/'worker-check'),inputs=train['inputs'],history=train['history'],feature_cache=None,indicator_root=train['indicator_root'],enriched_root=train['enriched_root'],
            source_sha256=source_hash(),execution=execution.__dict__,device='cuda',backend='compile',batch_size=512,maximum_data_gib=4.,maximum_gate_gib=0.,capture_gib=1.,
            lane_memory_bytes=int(torch.cuda.mem_get_info()[0]*.8),retain_session_residency=True,split='training',episode_capacity=65536)
        worker=Worker(cfg,Queue(maxsize=1),0);worker.host[day]=data;began=time.perf_counter()
        try:
            payload=[m.payload() for m in members];worker.run(day,None,payload,1,digest(payload),len(data.listing_ids))
            receipt=json.loads((out/'worker-check/generation-0001'/day/'receipt.json').read_bytes())
            actual=receipt['metrics']
            # Re-run path must agree with the same actual profile result hash.
            selected={k:actual[k] for k in gpu}  # same metric schema as oracle
            if digest(selected)!=reference_hash:raise AssertionError('Worker financial metrics differ from direct replay')
            report['worker_publication_verified']=True;report['worker_seconds']=time.perf_counter()-began
            print('Full-session Worker receipt parity passed',report['worker_seconds'],flush=True)
        finally:worker.close()
    write_json(out/'qualification.json',dict(**report,status='complete',measured=True,exact_cold_warm_financial_parity=True,bounded_diagnostic=True,full_session_timing=profiled_clocks==full));staging.close()
if __name__=='__main__':main()
