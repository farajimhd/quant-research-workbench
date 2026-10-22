"""Short two-GPU qualification cases; no multi-generation optimization."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json,time,gc
from pathlib import Path
import numpy as np,torch,psutil
from .distributed_assets import ensure_day,sha
from .distributed_bus import read,digest
from .publication import write_json

def export(result):return {k:v.cpu().tolist() if isinstance(v,torch.Tensor) else v for k,v in result.items()}

def main():
    p=argparse.ArgumentParser();p.add_argument('--job',type=Path,required=True);p.add_argument('--node',required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();job=read(a.job)
    from .run_search import source_hash,bind_dataset
    from .runtime import configure_caches
    from .windows_process import quiet_compilers
    quiet_compilers();torch.set_num_threads(1);out=a.output;out.mkdir(parents=True,exist_ok=False)
    assert source_hash()==job['source_sha256'];machine=job['machines'][a.node]
    ensure_day(job,a.node,'training','2026-08-04');train=bind_dataset(job['training'],'training')
    configure_caches(out/'compiler-cache',recompile_limit=128)
    from .data import SessionData
    from .io_pipeline import prepare_host,TransferStaging
    began=time.perf_counter();data=prepare_host(SessionData(Path(train['inputs'])/'2026-08-04',Path(train['history'])/'2026-08-04',device='cpu',feature_cache=Path(train['feature_cache'])/'2026-08-04' if train.get('feature_cache') else None,indicator_root=Path(train['indicator_root'])/'2026-08-04',enriched_root=Path(train['enriched_root'])/'2026-08-04'),resident=True)
    report=dict(node=a.node,source_sha256=source_hash(),host_load_seconds=time.perf_counter()-began,full_clocks=data.clocks,cases=[],gpu=torch.cuda.get_device_name(),qualification_clocks=128)
    staging=TransferStaging();began=time.perf_counter();staging.activate([('2026-08-04',data)],torch.device('cuda'));torch.cuda.synchronize();report['upload_seconds']=time.perf_counter()-began;data.clocks=129
    from .genome import Individual
    from .network import NetworkPlan
    from .network_pack import pack_networks
    from .evaluator import replay_cohort,Execution
    assert sha(job['profile_pool'])==job['profile_pool_sha256'];saved=read(job['profile_pool']);indices=np.column_stack((np.linspace(0,3071,512,dtype=int),np.linspace(3072,6143,512,dtype=int))).ravel()
    payload=[dict(saved['population'][int(i)],version='v42-recurrent-v1') for i in indices]
    began=time.perf_counter();members=[Individual.restore(v) for v in payload];packed=pack_networks(members);report['decode_pack_seconds']=time.perf_counter()-began
    from .distributed_preparation import PopulationPreparation
    prep=PopulationPreparation();t=time.perf_counter();f=prep.submit(payload,digest(payload));_,_,actual,_=f.result();report['cache_cold_seconds']=time.perf_counter()-t;t=time.perf_counter();assert prep.submit(payload,digest(payload)) is f;f.result();report['cache_hit_seconds']=time.perf_counter()-t;prep.close()
    for key in packed:
        if key=='values':
            for x,y in zip(packed[key],actual[key]):np.testing.assert_array_equal(x,y)
        else:np.testing.assert_array_equal(packed[key],actual[key])
    reference=None
    try:
        for batch in (512,1024):
            t=time.perf_counter();plan=NetworkPlan(members[:batch],'cuda');report.setdefault('plan_prepare_seconds',{})[str(batch)]=time.perf_counter()-t
            prior=None
            try:
                for passno in range(2):
                    gc.collect();torch.cuda.empty_cache();torch.cuda.reset_peak_memory_stats();t=time.perf_counter()
                    result=export(replay_cohort([data],members[:batch],[plan],execution=Execution(fee_per_share=.005,minimum_order_fee=1),backend='compile',trace=True,episode_capacity=65536)[0]);torch.cuda.synchronize();elapsed=time.perf_counter()-t
                    value=digest(result)
                    if prior:assert prior==value
                    prior=value
                    if passno:
                        prefix=digest({k:v[:512] for k,v in result.items()})
                        if reference:assert prefix==reference
                        else:reference=prefix
                    report['cases'].append(dict(batch=batch,pass_index=passno,seconds=elapsed,financial_sha256=value,peak_gpu_gib=torch.cuda.max_memory_allocated()/2**30,available_host_gib=psutil.virtual_memory().available/2**30))
                    write_json(out/'progress.json',report);print(report['cases'][-1],flush=True)
            finally:plan.close()
        report.update(exact_pack_parity=True,exact_batch_parity=True,exact_cold_warm_parity=True,bounded=True,full_session_timing_measured=False)
        write_json(out/'complete.json',report)
    finally:staging.close();data.close()
if __name__=='__main__':main()
