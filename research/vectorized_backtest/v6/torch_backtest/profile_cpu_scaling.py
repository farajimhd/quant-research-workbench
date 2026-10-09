"""Bounded all-training CPU prefix calibration; never full-session timing evidence."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime
import json
from pathlib import Path
from time import perf_counter
import psutil
import torch
from .profile_cpu_comparison import profile_one,numeric_parity,wait_gpu
from .run_structure import training_days
from .runtime import require_runtime,write_json,file_hash,code_hash
from .materialize import owned_run


def compare_prefix(reference,actual,*,gpu=False):
    cpu=json.loads((actual/'receipt.json').read_text())
    if cpu['validation_opened'] or cpu['full_session']:raise ValueError('Require a certified training-only prefix profile')
    if gpu:
        left_root=reference/'batch-000000'
        before=json.loads((left_root/'receipt.json').read_text())
        population=json.loads((reference.parent.parent/'population.json').read_text())
    else:
        left_root=reference;before=json.loads((left_root/'receipt.json').read_text())
        population=json.loads((reference/'population.json').read_text())['population']
        if before['arguments']['seconds']!=cpu['arguments']['seconds']:raise ValueError('CPU prefix length differs')
    for name in ('input_receipt_sha256','structural_receipt_sha256'):
        if before[name]!=cpu[name]:raise ValueError('Prefix source binding changed')
    if population!=json.loads((actual/'population.json').read_text())['population']:raise ValueError('Prefix population changed')
    left=torch.load(left_root/'fills.pt',weights_only=True,map_location='cpu')
    right=torch.load(actual/'fills.pt',weights_only=True,map_location='cpu')
    cutoff=None
    if gpu:
        source=Path(cpu['arguments']['inputs'])/cpu['day']/'complete.json'
        session=json.loads(source.read_text())['identity']['session']
        cutoff=datetime.fromisoformat(session['start']).timestamp()+cpu['arguments']['seconds']
    exact=True;maximum=0.
    for lane,count in enumerate(left['counts'].tolist()):
        a=left['ledger'][lane,:count]
        if cutoff is not None:a=a[a[:,0]<cutoff]
        b=right['ledger'][lane,:int(right['counts'][lane])]
        if a.shape!=b.shape or not torch.equal(a[:,[0,1,2,3,4,7,8]],b[:,[0,1,2,3,4,7,8]]):
            raise ValueError('Prefix economic event identities/counts/order changed')
        if not torch.allclose(a[:,5:7],b[:,5:7],rtol=1e-12,atol=1e-8):raise ValueError('Prefix fill economics changed')
        exact=exact and torch.equal(a,b)
        if len(a):maximum=max(maximum,float((a-b).abs().max()))
    if not gpu:
        for name,value in before['metrics'].items():
            if name=='replay_seconds':continue
            if name not in cpu['metrics'] or not numeric_parity(value,cpu['metrics'][name]):raise ValueError('Prefix financial metrics changed: '+name)
            exact=exact and value==cpu['metrics'][name]
    return dict(day=cpu['day'],reference_device='gpu' if gpu else 'cpu',seconds=cpu['arguments']['seconds'],
        full_session=False,actual_fill_identities_and_counts='exact',numeric_values_exact=exact,
        maximum_fill_absolute_difference=maximum,numeric_tolerance=dict(rtol=1e-12,atol=1e-8),
        reference_receipt_sha256=file_hash(left_root/'receipt.json'),actual_receipt_sha256=file_hash(actual/'receipt.json'))


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('inputs','structure','output','gpu-reference'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--session-count',type=int,default=30);p.add_argument('--seconds',type=int,default=512)
    p.add_argument('--population',type=int,default=128);p.add_argument('--seed',type=int,default=2236)
    p.add_argument('--cpu-configs',default='16x4,16x8,30x2,30x4,8x16')
    p.add_argument('--maximum-state-gib',type=float,default=.5);p.add_argument('--rule-workspace-gib',type=float,default=.5)
    a=p.parse_args(argv);logical=os.cpu_count();physical=psutil.cpu_count(logical=False)
    configs=[tuple(map(int,v.split('x'))) for v in a.cpu_configs.split(',')]
    if not 2<=a.session_count<=30 or not 1<=a.seconds<19800 or not 1<=a.population<=1024 or any(len(v)!=2 or min(v)<1 or v[0]>a.session_count or v[0]*v[1]>logical for v in configs):
        raise ValueError('CPU calibration exceeds training session/logical thread bounds')
    root=require_runtime(a.output)
    if (root/'identity.json').exists():raise ValueError('Fresh CPU calibration identity required')
    with owned_run(root,version='v6-cpu-scaling-prefix-v1'):
        wait_gpu(root,a.gpu_reference)
        days=training_days(a.inputs)[:a.session_count]
        write_json(root/'identity.json',dict(code_sha256=code_hash(),physical_cores=physical,logical_processors=logical,
            total_ram_bytes=psutil.virtual_memory().total,available_ram_bytes=psutil.virtual_memory().available,
            arguments={k:str(v) if isinstance(v,Path) else v for k,v in vars(a).items()},validation_opened=False,full_session=False))
        rows=[];audits=[]
        for label,workers,threads in [('baseline-4x1',min(4,len(days)),1)]+[(f'cpu-{w}x{t}',w,t) for w,t in configs]:
            envelope=workers*(1+a.maximum_state_gib+a.rule_workspace_gib+1)*1024**3
            if envelope>psutil.virtual_memory().available*.70:
                rows.append(dict(configuration=label,status='rejected_memory_envelope',required_bytes=envelope))
                write_json(root/'measurements.json',rows)
                if label=='baseline-4x1':raise MemoryError('CPU baseline memory unavailable')
                continue
            folder=require_runtime(root/label);started=perf_counter();results=[];failures=[]
            write_json(root/'status.json',dict(stage=label,workers=workers,threads_per_worker=threads,active=0,queued=len(days),completed=0,failed=0,seconds=a.seconds,full_session=False))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures={pool.submit(profile_one,folder,day,a,threads):day for day in days}
                for future in as_completed(futures):
                    day=futures[future]
                    try:
                        result=future.result()
                        if label!='baseline-4x1':audits.append(dict(configuration=label,**compare_prefix(root/'baseline-4x1'/day,folder/day)))
                        reference=a.gpu_reference/'serial-warm'/day
                        if (reference/'receipt.json').exists():audits.append(dict(configuration=label,**compare_prefix(reference,folder/day,gpu=True)))
                        results.append(result)
                    except Exception as error:
                        failures.append(dict(day=day,error=str(error)));write_json(root/'failures.json',failures)
                    remaining=len(days)-len(results)-len(failures)
                    status=dict(stage=label,workers=workers,threads_per_worker=threads,active=min(workers,remaining),queued=max(0,remaining-workers),completed=len(results),failed=len(failures),seconds=a.seconds,full_session=False)
                    write_json(root/'status.json',status);print(status,flush=True)
            if failures:raise RuntimeError('CPU calibration failed; see failures.json')
            rows.append(dict(configuration=label,workers=workers,threads_per_worker=threads,total_torch_threads=workers*threads,
                sessions=len(days),seconds=a.seconds,elapsed_seconds=perf_counter()-started,results=results,full_session=False))
            write_json(root/'measurements.json',rows);write_json(root/'comparisons.json',audits)
        write_json(root/'receipt.json',dict(status='complete',measurements=rows,audits=audits,validation_opened=False,optimization_started=False,
            full_session=False,gpu_reference_receipt_sha256=file_hash(a.gpu_reference/'receipt.json'),
            limitations=['512-clock calibration is not full-session throughput evidence',
                'GPU prefix fill comparison covers only sessions present in GPU reference; all sessions compare to audited CPU baseline',
                'Full causal rule preparation is included despite partial financial replay']))
        write_json(root/'status.json',dict(stage='CPU scaling calibration complete',status='complete',full_session=False))
    return 0


if __name__=='__main__':raise SystemExit(main())
