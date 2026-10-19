"""Finite real-input CPU/CUDA parity and matched cold/warm laptop timings."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json,time,gc,copy
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
from research.vectorized_backtest.v6.torch_backtest.runtime import write_json,require_runtime
from .runtime import configure_caches
from .windows_process import quiet_compilers
quiet_compilers()

class RecordingPlan(NetworkPlan):
    """Record actual GPU inference inputs to the financial transition oracle."""
    def __init__(self,members):
        super().__init__(members,'cuda');self.tape=[]
    def signals(self,*args,**kwargs):
        result=super().signals(*args,**kwargs)
        self.tape.append((result.cpu().clone(),self.ratios.cpu().clone()))
        return result

class TapePlan:
    total_nodes=0
    def __init__(self,tape):self.tape=tape
    def prepare(self,b,u):self.ratios=torch.ones((1,b,u,3),dtype=torch.float64)
    def reset(self):self.cursor=0
    def signals(self,*args,out=None):
        signals,ratios=self.tape[self.cursor];self.cursor+=1;self.ratios.copy_(ratios)
        if out is not None:out.copy_(signals);return out
        return signals
    def record_outcomes(self,*args):pass

def compare_financial(reference,actual):
    differences={}
    for k,v in reference.items():
        if isinstance(v,torch.Tensor):
            other=actual[k].cpu()
            if not torch.equal(v.cpu(),other):
                if v.dtype==torch.bool:raise AssertionError('Boolean financial mismatch: '+k)
                differences[k]=float((v.cpu()-other).abs().max())
                torch.testing.assert_close(v.cpu(),other,rtol=1e-10,atol=1e-9)
        elif v!=actual[k]:raise AssertionError('Exact episode parity failed: '+k)
    return differences

def main():
    p=argparse.ArgumentParser();p.add_argument('--day',default='2026-07-30');p.add_argument('--training',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--clocks',type=int,default=256);p.add_argument('--batches',default='128,256,512');p.add_argument('--kernel-comparison',action='store_true');args=p.parse_args()
    torch.set_num_threads(1);out=require_runtime(args.output);configure_caches(require_runtime(Path('D:/TradingML/runtimes/v32-cache')/f'p{os.getpid()}'),recompile_limit=128);train=bind_dataset(args.training,'training');day=args.day
    started=time.perf_counter();print('Loading certified '+day+' inputs',flush=True)
    data=prepare_host(SessionData(Path(train['inputs'])/day,Path(train['history'])/day,device='cpu',maximum_gib=4.,indicator_root=Path(train['indicator_root'])/day,enriched_root=Path(train['enriched_root'])/day),resident=True)
    print(f'Loaded {time.perf_counter()-started:.2f}s; resident {data.resident_features.bytes/2**30:.2f} GiB; RAM free {psutil.virtual_memory().available/2**30:.2f} GiB',flush=True)
    report=dict(source_sha256=source_hash(),neural_cuda_block=int(os.environ.get("V32_NEURAL_BLOCK","128")),load_seconds=time.perf_counter()-started,identity=data.identity,full_clocks=data.clocks,listings=len(data.listing_ids),host_bank_bytes=data.resident_features.bytes)
    full=data.clocks;data.clocks=64;members=sample(np.random.default_rng(2020),4);execution=Execution(fee_per_share=.005,minimum_order_fee=1)
    # Retain the CPU authority before upload mutates device tensor dictionaries.
    cpu_data=copy.copy(data);cpu_data.resident_features=copy.copy(data.resident_features)
    cpu_data.resident_features.column_plans={}
    staging=TransferStaging();upload=time.perf_counter();staging.activate([(day,data)],torch.device('cuda'));torch.cuda.synchronize();report['upload_seconds']=time.perf_counter()-upload
    # Float32 inference is qualified separately from float64 finance. Continuous
    # sizing exposes ordinary CPU/CUDA tanh rounding; never hide it by widening
    # the financial oracle's tolerances.
    neural=[NetworkPlan(members,d) for d in ('cpu','cuda')]
    report['neural_cpu_cuda']={'maximum_fraction_difference':0.,'gate_mismatches':0,'clocks':3}
    for tick in range(3):
        decisions=[];fractions=[]
        for plan,item in zip(neural,(cpu_data,data)):
            position=torch.zeros((1,4,len(data.listing_ids),9),device=plan.device,dtype=torch.float64)
            opened=torch.zeros(position.shape[:-1],device=plan.device,dtype=torch.float64)
            decisions.append(plan.signals(item,torch.tensor(tick,device=plan.device),position,opened,item.tensors['mark'][tick][None,None]).cpu())
            fractions.append(plan.ratios.cpu().clone())
        report['neural_cpu_cuda']['gate_mismatches']+=int((decisions[0]!=decisions[1]).sum())
        report['neural_cpu_cuda']['maximum_fraction_difference']=max(report['neural_cpu_cuda']['maximum_fraction_difference'],float((fractions[0]-fractions[1]).abs().max()))
        torch.testing.assert_close(fractions[0],fractions[1],rtol=0,atol=2e-6)
    if report['neural_cpu_cuda']['gate_mismatches']:raise AssertionError('Bounded neural gate fixture differs')
    for plan in neural:plan.close()
    print('Neural float32 comparison',report['neural_cpu_cuda'],flush=True)
    print('Matched-decision CPU financial oracle (4 candidates, 63 clocks, all listings)',flush=True)
    recording=RecordingPlan(members)
    eager=replay_cohort([data],members,[recording],execution=execution,trace=True,episode_capacity=65536)[0]
    cpu=replay_cohort([cpu_data],members,[TapePlan(recording.tape)],execution=execution,trace=True,episode_capacity=65536)[0]
    report['cpu_cuda_differences']=compare_financial(cpu,eager)
    recording.close();del recording
    gpu=replay_cohort([data],members,[NetworkPlan(members,'cuda')],execution=execution,backend='compile',trace=True,episode_capacity=65536)[0]
    report['eager_captured_differences']=compare_financial(eager,gpu)
    report['cpu_reference_passed']=True;report['exact_episode_parity']=True
    print('CPU/CUDA financial oracle passed; differences: '+str(report['cpu_cuda_differences']),flush=True);data.clocks=min(full,args.clocks);report['cases']=[]
    matched_prefix=None
    for batch in map(int,args.batches.split(',')):
        members=sample(np.random.default_rng(2020),batch);plan=NetworkPlan(members,'cuda');times=[]
        reference_hash=None
        for passno in range(2):
            gc.collect();torch.cuda.empty_cache();torch.cuda.reset_peak_memory_stats();began=time.perf_counter()
            result=replay_cohort([data],members,[plan],execution=execution,backend='compile',trace=True,episode_capacity=65536)[0]
            torch.cuda.synchronize();elapsed=time.perf_counter()-began;times.append(elapsed)
            financial_hash=digest({k:v.cpu().tolist() if isinstance(v,torch.Tensor) else v for k,v in result.items()})
            exported={k:v.cpu().tolist() if isinstance(v,torch.Tensor) else v for k,v in result.items()}
            row_hashes=[digest({k:v[j] for k,v in exported.items()}) for j in range(batch)]
            if passno==1:
                if matched_prefix is None:matched_prefix=row_hashes
                elif row_hashes[:len(matched_prefix)]!=matched_prefix:raise AssertionError('Batch width changed candidate financial results')
            if reference_hash is None:reference_hash=financial_hash
            elif reference_hash!=financial_hash:raise AssertionError('Cold/warm financial metric hashes differ')
            case=dict(batch=batch,pass_index=passno,clocks=data.clocks-1,seconds=elapsed,financial_hash=financial_hash,row_hashes=row_hashes,peak_allocated=torch.cuda.max_memory_allocated(),peak_reserved=torch.cuda.max_memory_reserved(),available_gib=psutil.virtual_memory().available/2**30)
            report['cases'].append(case);print(case,flush=True);write_json(out/'profile-progress.json',report);del result
        plan.close()
    if args.kernel_comparison:
        # Excluded from replay timings. Same real clock, candidate payloads and
        # position fixtures for all kernels; no candidates enter training.
        plan=NetworkPlan(members,'cuda');u=len(data.listing_ids);b=len(members)
        position=torch.zeros((1,b,u,9),device='cuda',dtype=torch.float64)
        opened=torch.zeros((1,b,u),device='cuda',dtype=torch.float64)
        index=torch.tensor(min(100,data.clocks-1),device='cuda');previous=data.tensors['mark'][index][None,None]
        report['kernel_comparison']=[];reference=None
        plan.prepare(b,u)
        for block in (32,64,128,256):
            plan.cuda_block=block;plan.reset()
            value=plan.signals(data,index,position,opened,previous);torch.cuda.synchronize()
            gate_hash=digest(dict(gates=value.cpu().tolist(),ratios=plan.ratios.cpu().tolist()))
            if reference is None:reference=gate_hash
            elif gate_hash!=reference:raise AssertionError('Neural kernel exact gate parity failed')
            stream=torch.cuda.Stream();stream.wait_stream(torch.cuda.current_stream())
            with torch.cuda.stream(stream),torch.inference_mode():
                plan.signals(data,index,position,opened,previous)
                graph=torch.cuda.CUDAGraph()
                with torch.cuda.graph(graph,stream=stream):plan.signals(data,index,position,opened,previous)
            torch.cuda.current_stream().wait_stream(stream)
            for _ in range(10):graph.replay()
            begin=torch.cuda.Event(enable_timing=True);end=torch.cuda.Event(enable_timing=True)
            begin.record()
            for _ in range(200):graph.replay()
            end.record();end.synchronize()
            result=dict(engine='ragged-recurrent',block=block,milliseconds_per_clock=begin.elapsed_time(end)/200,exact_gate_sha256=gate_hash)
            report['kernel_comparison'].append(result);print(result,flush=True)
            del graph;gc.collect();torch.cuda.empty_cache()
        # Availability diagnostics are measured separately from financial replay.
        report['input_diagnostics']=[]
        plan.register_engine=False
        for tick in (0,min(100,full-1),min(200,full-1)):
            index.fill_(tick);values,valid=plan.project(data,index);c=len(plan.columns)
            value=plan.signals(data,index,position,opened,previous)
            report['input_diagnostics'].append(dict(clock=tick,observed_fraction=float(valid.float().mean()),
                missing_values_neutral=bool((values[:,:c][~valid]==0).all() and (values[:,c:2*c][~valid]==0).all()),
                entry_active_fraction=float(((value&1)!=0).float().mean()),
                absolute_cross_listing_std_median=float(values[:,:c].std(0).median()),
                relative_cross_listing_std_median=float(values[:,c:2*c].std(0).median())))
        print('Input diagnostics',report['input_diagnostics'],flush=True);plan.close()
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
