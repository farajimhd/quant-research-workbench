"""Short matched-template/random CUDA check; never an optimization campaign."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json,time,gc
from pathlib import Path
import numpy as np
import torch,psutil
from .data import SessionData
from .io_pipeline import prepare_host,TransferStaging
from .network import NetworkPlan
from .genome import Individual
from .evaluator import replay_cohort,Execution
from .run_search import source_hash,bind_dataset,digest
from .runtime import configure_caches
from .windows_process import quiet_compilers
from research.vectorized_backtest.v6.torch_backtest.runtime import write_json,require_runtime


def export(result):return {k:v.cpu().tolist() if isinstance(v,torch.Tensor) else v for k,v in result.items()}


def main():
    p=argparse.ArgumentParser();p.add_argument('--training',type=Path,required=True);p.add_argument('--pool',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    quiet_compilers();torch.set_num_threads(1);out=require_runtime(a.output)
    configure_caches(out/'compiler-cache',recompile_limit=128)
    saved=json.loads(a.pool.read_bytes());assert saved['source_sha256']==source_hash()
    train=bind_dataset(a.training,'training');day='2026-08-04'
    if psutil.virtual_memory().available<20*2**30:raise MemoryError('Laptop 12 GiB reserve + 8 GiB admission unavailable')
    began=time.perf_counter();data=prepare_host(SessionData(Path(train['inputs'])/day,Path(train['history'])/day,device='cpu',indicator_root=Path(train['indicator_root'])/day,enriched_root=Path(train['enriched_root'])/day),resident=True)
    report=dict(source_sha256=source_hash(),host_load_seconds=time.perf_counter()-began,full_session_clocks=data.clocks,listing_count=len(data.listing_ids),cases=[])
    print('Host loaded',report,flush=True);staging=TransferStaging();began=time.perf_counter();staging.activate([(day,data)],torch.device('cuda'));torch.cuda.synchronize();report['upload_seconds']=time.perf_counter()-began
    data.clocks=65;execution=Execution(fee_per_share=.005,minimum_order_fee=1)
    # Half templates, half current random, evenly spanning each half of the
    # actual fresh population. The 512 fixture is a prefix of the 1024 fixture.
    fixture=np.column_stack((np.linspace(0,3071,512,dtype=int),np.linspace(3072,6143,512,dtype=int))).ravel()
    members=[Individual.restore(saved['population'][int(i)]) for i in fixture]
    try:
        from .composition import mutation
        from .schedule import Proposal
        from .mask_genome import network,MODES
        from dataclasses import replace
        small=[];cases=[]
        for mode in MODES:
            for replica in range(2):
                for attempt in range(128):
                    parent=members[(attempt+replica)%len(members)];donor=members[(attempt+replica+7)%len(members)]
                    try:net=network(Proposal(bytes(32),(replica,attempt)),parent.networks['entry'],donor.networks['entry'],mode)
                    except ValueError:continue
                    small.append(replace(parent,networks=dict(parent.networks,entry=net)).validate());cases.append(mode);break
                else:raise ValueError('No valid bounded mask fixture: '+mode)
        report['newborn_fixture_cases']=cases
        report['newborn_fixture_sha256']=digest([m.payload() for m in small])
        plan=NetworkPlan(small,'cuda')
        eager=export(replay_cohort([data],small,[plan],execution=execution,trace=True,episode_capacity=65536)[0]);plan.close()
        plan=NetworkPlan(small,'cuda')
        compiled=export(replay_cohort([data],small,[plan],execution=execution,backend='compile',trace=True,episode_capacity=65536)[0]);plan.close()
        if digest(eager)!=digest(compiled):
            write_json(out/'eager-result.json',eager);write_json(out/'captured-result.json',compiled)
            differences={}
            for key in eager:
                if eager[key]!=compiled[key]:
                    try:differences[key]=float(np.max(np.abs(np.asarray(eager[key])-np.asarray(compiled[key]))))
                    except (ValueError,TypeError):differences[key]='episode/list mismatch'
            write_json(out/'eager-captured-differences.json',differences)
            report['eager_captured_differences']=differences
        report['exact_eager_captured_financial_parity']=digest(eager)==digest(compiled)
        # The production authority is V34's captured replay, not a different
        # eager arithmetic ordering. Require exact parity in that same mode.
        from research.vectorized_backtest.v34.genome import Individual as BaselineIndividual
        from research.vectorized_backtest.v34.network import NetworkPlan as BaselinePlan
        from research.vectorized_backtest.v34.evaluator import replay_cohort as baseline_replay
        baseline=[BaselineIndividual.restore(dict(m.payload(),version='v34-recurrent-v1')) for m in small]
        baseline_plan=BaselinePlan(baseline,'cuda')
        try:baseline_result=export(baseline_replay([data],baseline,[baseline_plan],execution=execution,backend='compile',trace=True,episode_capacity=65536)[0])
        finally:baseline_plan.close()
        assert digest(baseline_result)==digest(compiled),'V34/V42 captured financial or episode mismatch'
        report['exact_v34_captured_financial_parity']=True
        reference=None
        for batch in (512,1024):
            selected=members[:batch];plan=NetworkPlan(selected,'cuda');previous=None
            try:
                for passno in range(2):
                    gc.collect();torch.cuda.empty_cache();torch.cuda.reset_peak_memory_stats();began=time.perf_counter()
                    result=export(replay_cohort([data],selected,[plan],execution=execution,backend='compile',trace=True,episode_capacity=65536)[0]);torch.cuda.synchronize();seconds=time.perf_counter()-began
                    value=digest(result)
                    if previous is not None:assert value==previous,'Cold/warm financial mismatch'
                    previous=value
                    if passno==1:
                        prefix={k:v[:512] for k,v in result.items()}
                        if reference is None:reference=digest(prefix)
                        else:assert reference==digest(prefix),'Batch width changed results'
                    case=dict(batch=batch,pass_index=passno,replay_clocks=64,seconds=seconds,financial_sha256=value,peak_allocated_gib=torch.cuda.max_memory_allocated()/2**30,peak_reserved_gib=torch.cuda.max_memory_reserved()/2**30,available_host_gib=psutil.virtual_memory().available/2**30)
                    report['cases'].append(case);print(case,flush=True);write_json(out/'progress.json',report)
            finally:plan.close()
        report.update(exact_batch_parity=True,exact_cold_warm_parity=True,bounded=True,full_session_timing_measured=False)
        write_json(out/'complete.json',report)
    finally:staging.close()


if __name__=='__main__':main()
