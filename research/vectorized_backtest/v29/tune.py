"""Finite matched batch diagnostics against an explicitly pinned V29 engine.

Run this file directly with --engine-code so baseline and candidate engines run
in isolated processes. Never launches evolution or imports evolved fixtures as
training initialization. Diagnostic hashes include every financial output.
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,gc,hashlib,json,sys,time
from pathlib import Path


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--engine-code',type=Path,required=True)
    p.add_argument('--training',type=Path,required=True)
    p.add_argument('--fixture',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--batch',type=int,required=True)
    p.add_argument('--clocks',type=int,default=257)
    p.add_argument('--passes',type=int,default=2)
    p.add_argument('--audit',action='store_true')
    p.add_argument('--components',action='store_true')
    a=p.parse_args()
    if a.batch not in (512,1024,2048,4096) or a.passes not in (1,2):p.error('Finite batch/pass envelope required')
    if a.audit and a.clocks!=129:p.error('Audit uses exactly 128 transitions without capture padding')
    sys.path.insert(0,str(a.engine_code.resolve()))
    import torch,psutil
    from research.vectorized_backtest.v29.data import SessionData
    from research.vectorized_backtest.v29.io_pipeline import prepare_host,TransferStaging
    from research.vectorized_backtest.v29.network import NetworkPlan
    from research.vectorized_backtest.v29.genome import Individual
    from research.vectorized_backtest.v29.evaluator import replay_cohort,Execution
    from research.vectorized_backtest.v29.run_search import bind_dataset,source_hash,digest
    from research.vectorized_backtest.v6.torch_backtest.runtime import write_json,configure_caches,require_runtime
    torch.set_num_threads(1)
    out=require_runtime(a.output)
    if (out/'result.json').exists():raise ValueError('Never overwrite diagnostic evidence')
    configure_caches(require_runtime(Path('D:/TradingML/runtimes/v29-cache')/f't{os.getpid()}'),recompile_limit=128)
    fixture=json.loads(a.fixture.read_bytes());payload=fixture['population']
    if digest(payload)!=fixture['population_sha256']:raise ValueError('Fixture identity changed')
    if len(payload)!=1024:raise ValueError('Exactly 1024 matched diagnostic candidates required')
    members=[Individual.restore(v) for v in payload]
    n=max(len(members),a.batch);members=(members*((n+len(members)-1)//len(members)))[:n]
    train=bind_dataset(a.training,'training');day='2026-08-04'
    if psutil.virtual_memory().available<20*2**30:raise MemoryError('Preserve laptop reserve12 + admission8 GiB')
    started=time.perf_counter()
    data=prepare_host(SessionData(Path(train['inputs'])/day,Path(train['history'])/day,device='cpu',maximum_gib=4.,indicator_root=Path(train['indicator_root'])/day,enriched_root=Path(train['enriched_root'])/day),resident=True)
    load=time.perf_counter()-started;full=data.clocks;data.clocks=min(full,a.clocks)
    staging=TransferStaging();started=time.perf_counter();staging.activate([(day,data)],torch.device('cuda'));torch.cuda.synchronize();upload=time.perf_counter()-started
    report=dict(source_sha256=source_hash(),harness_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),fixture_sha256=hashlib.sha256(a.fixture.read_bytes()).hexdigest(),population_sha256=fixture['population_sha256'],batch=a.batch,candidates=n,clocks=data.clocks-1,full_session=data.clocks==full,audit=a.audit,load_seconds=load,upload_seconds=upload,cases=[],identity=data.identity)

    class AuditPlan(NetworkPlan):
        def prepare(self,b,u):
            super().prepare(b,u)
            if not hasattr(self,'audit_gates'):
                required=128*b*u*26
                if required>torch.cuda.mem_get_info()[0]*.5:raise MemoryError('Bounded audit tape exceeds GPU headroom')
                self.audit_gates=torch.empty((128,b,u),device='cuda',dtype=torch.int16)
                self.audit_ratios=torch.empty((128,b,u,3),device='cuda',dtype=torch.float64)
        def signals(self,data,index,*args,**kwargs):
            value=super().signals(data,index,*args,**kwargs)
            self.audit_gates.index_copy_(0,index.reshape(1),value)
            self.audit_ratios.index_copy_(0,index.reshape(1),self.ratios)
            return value
        def hashes(self):
            result=[]
            for i in range(len(self.members)):
                fields={}
                for name,tensor in (('gates',self.audit_gates),('ratios',self.audit_ratios)):
                    # Per-candidate tape is below 2 MiB; no unbounded host copy.
                    raw=tensor[:,i].contiguous().cpu().numpy().tobytes()
                    if len(raw)>32*2**20:raise MemoryError('Audit transfer slice exceeded bound')
                    fields[name+'_sha256']=hashlib.sha256(raw).hexdigest()
                result.append(fields)
            return result
        def close(self):
            super().close()
            self.audit_gates=None;self.audit_ratios=None

    execution=Execution(fee_per_share=.005,minimum_order_fee=1)
    try:
        began=time.perf_counter()
        plans=[(AuditPlan if a.audit else NetworkPlan)(members[i:i+a.batch],'cuda') for i in range(0,n,a.batch)]
        torch.cuda.synchronize();report['plan_pack_seconds']=time.perf_counter()-began
        for passno in range(a.passes):
            rows=[];audits=[];evaluation=0.;export=0.
            gc.collect();torch.cuda.empty_cache();torch.cuda.reset_peak_memory_stats()
            for i,plan in zip(range(0,n,a.batch),plans):
                began=time.perf_counter()
                result=replay_cohort([data],members[i:i+a.batch],[plan],execution=execution,backend='compile',trace=True,episode_capacity=65536)[0]
                torch.cuda.synchronize();evaluation+=time.perf_counter()-began;began=time.perf_counter()
                values={k:v.cpu().tolist() if isinstance(v,torch.Tensor) else v for k,v in result.items()}
                rows.extend(digest({k:v[j] for k,v in values.items()}) for j in range(a.batch))
                export+=time.perf_counter()-began
                if a.audit:audits.extend(plan.hashes())
                del result,values
            case=dict(pass_index=passno,evaluation_seconds=evaluation,export_hash_seconds=export,candidates_per_second=n/evaluation,financial_hashes=rows,gate_ratio_hashes=audits,peak_allocated=torch.cuda.max_memory_allocated(),peak_reserved=torch.cuda.max_memory_reserved(),available_host_gib=psutil.virtual_memory().available/2**30)
            if passno and rows!=report['cases'][0]['financial_hashes']:raise AssertionError('Cold/warm financial results differ')
            report['cases'].append(case);write_json(out/'progress.json',report)
            print({k:v for k,v in case.items() if not k.endswith('hashes')},flush=True)
        if a.components:
            plan=plans[0];b=len(plan.members);u=len(data.listing_ids)
            position=torch.zeros((1,b,u,9),device='cuda',dtype=torch.float64);opened=torch.zeros((1,b,u),device='cuda',dtype=torch.float64)
            index=torch.tensor(100,device='cuda');previous=data.tensors['mark'][100][None,None]
            def measure(fn):
                stream=torch.cuda.Stream();stream.wait_stream(torch.cuda.current_stream())
                with torch.cuda.stream(stream),torch.inference_mode():
                    for _ in range(3):fn()
                    graph=torch.cuda.CUDAGraph()
                    with torch.cuda.graph(graph,stream=stream):fn()
                torch.cuda.current_stream().wait_stream(stream)
                for _ in range(10):graph.replay()
                start=torch.cuda.Event(enable_timing=True);end=torch.cuda.Event(enable_timing=True);start.record()
                for _ in range(100):graph.replay()
                end.record();end.synchronize();ms=start.elapsed_time(end)/100
                del graph;return ms
            report['components_ms']=dict(feature_projection=measure(lambda:plan.project(data,index)),recurrent_copy=measure(lambda:(plan.memory.copy_(plan.workspace),plan.memory_known.copy_(plan.known))),whole_neural_step=measure(lambda:plan.signals(data,index,position,opened,previous)))
            print('Component milliseconds',report['components_ms'],flush=True)
        report['status']='complete';write_json(out/'result.json',report)
    finally:
        for plan in locals().get('plans',[]):plan.close()
        staging.close()


if __name__=='__main__':main()
