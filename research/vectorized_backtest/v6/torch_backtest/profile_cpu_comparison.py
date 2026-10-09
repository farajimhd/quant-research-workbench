"""Isolated multicore CPU/full-session comparison against a sealed GPU profile.

Runs independent subprocess accounts, with bounded Torch threads per process.
Waits for the exact GPU profile to finish so CPU load does not distort its timings.
Never evaluates sealed sessions or launches optimization.
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime
import json
import math
from pathlib import Path
import subprocess
import sys
from time import perf_counter,sleep
import psutil
import torch
from .runtime import require_runtime,write_json,file_hash,code_hash
from .materialize import owned_run
from .run_structure import training_days


def numeric_parity(left,right):
    if isinstance(left,list) or isinstance(right,list):
        return isinstance(left,list) and isinstance(right,list) and len(left)==len(right) and all(numeric_parity(a,b) for a,b in zip(left,right))
    if left==right:return True
    if isinstance(left,(int,float)) and isinstance(right,(int,float)):
        if isinstance(left,int) and isinstance(right,int):return False
        return math.isclose(left,right,rel_tol=1e-12,abs_tol=1e-8)
    return False


def compare_device(reference,actual):
    gpu=json.loads((reference/'batch-000000'/'receipt.json').read_text())
    cpu=json.loads((actual/'receipt.json').read_text())
    if not cpu['full_session'] or cpu['validation_opened']:raise ValueError('Require full training-only CPU receipt')
    if cpu['input_receipt_sha256']!=gpu['input_receipt_sha256']:raise ValueError('CPU/GPU source changed')
    if cpu['structural_receipt_sha256']!=gpu['structural_receipt_sha256']:raise ValueError('CPU/GPU structural authority changed')
    population=json.loads((actual/'population.json').read_text())['population']
    expected=json.loads((reference.parent.parent/'population.json').read_text())
    if population!=expected:raise ValueError('CPU/GPU candidate population changed')
    left=torch.load(reference/'batch-000000'/'fills.pt',weights_only=True,map_location='cpu')
    right=torch.load(actual/'fills.pt',weights_only=True,map_location='cpu')
    if not torch.equal(left['counts'],right['counts']):raise ValueError('CPU/GPU fill count differs')
    exact=True;maximum=0.
    for lane,count in enumerate(left['counts'].tolist()):
        a,b=left['ledger'][lane,:count],right['ledger'][lane,:count]
        if not torch.equal(a[:,[0,1,2,3,4,7,8]],b[:,[0,1,2,3,4,7,8]]):raise ValueError('CPU/GPU economic event ordering/identity differs')
        if not torch.allclose(a[:,5:7],b[:,5:7],rtol=1e-12,atol=1e-8):raise ValueError('CPU/GPU fill price/fee differs materially')
        exact=exact and torch.equal(a,b)
        if count:maximum=max(maximum,float((a-b).abs().max()))
    for key,value in gpu['metrics'].items():
        if key=='inactivity_fraction':continue
        if key not in cpu['metrics']:raise ValueError('CPU financial metric missing: '+key)
        other=cpu['metrics'][key]
        if value==other:continue
        if not numeric_parity(value,other):
            raise ValueError('CPU/GPU financial metric differs: '+key)
        exact=False
    return dict(day=cpu['day'],actual_fill_counts_and_identities='exact',all_numeric_values_exact=exact,
        maximum_fill_absolute_difference=maximum,numeric_comparison=dict(rtol=1e-12,atol=1e-8),
        cpu_receipt_sha256=file_hash(actual/'receipt.json'),gpu_receipt_sha256=file_hash(reference/'batch-000000'/'receipt.json'))


def profile_one(root,day,args,threads):
    folder=require_runtime(root/day)
    command=[sys.executable,'-B','-u','-m','research.vectorized_backtest.v6.torch_backtest.profile_sparse',
        '--inputs',str(args.inputs),'--structure',str(args.structure/day),'--output',str(folder),'--day',day,
        '--device','cpu','--cpu-threads',str(threads),'--backend','eager','--batch-size',str(args.population),
        '--seconds',str(getattr(args,'seconds',19800)),'--repeats','1','--seed',str(args.seed),'--maximum-input-gib','1','--maximum-fills','16384',
        '--maximum-state-gib',str(getattr(args,'maximum_state_gib',4.)),
        '--rule-workspace-gib',str(getattr(args,'rule_workspace_gib',2.)),
        '--population-file',str(args.gpu_reference/'population.json')]
    started=perf_counter();peak=0
    with (folder/'worker.log').open('w') as log,(folder/'worker.err').open('w') as err:
        child=subprocess.Popen(command,stdout=log,stderr=err)
        proc=psutil.Process(child.pid)
        write_json(folder/'launch.json',dict(pid=child.pid,creation_epoch=proc.create_time(),command=command))
        try:
            while child.poll() is None:
                try:
                    info=proc.memory_info();peak=max(peak,info.rss,getattr(info,'peak_wset',0))
                except psutil.NoSuchProcess:pass
                sleep(.5)
            result=child.wait()
        finally:
            if child.poll() is None:child.terminate();child.wait()
    write_json(folder/'exit.json',dict(exit_code=result,elapsed_seconds=perf_counter()-started,peak_process_rss_bytes=peak))
    if result:raise RuntimeError('CPU profile failed: '+str(folder))
    receipt=json.loads((folder/'receipt.json').read_text())
    return dict(day=day,total_seconds=perf_counter()-started,peak_process_rss_bytes=peak,
        load_seconds=receipt['load_seconds'],rule_seconds=receipt['rule_seconds'],setup_seconds=receipt['setup_seconds'],
        replay_measurements=receipt['measurements'],receipt_sha256=file_hash(folder/'receipt.json'))


def wait_gpu(root,reference):
    launch=json.loads((reference/'launch.json').read_text());pid=launch['pid']
    started=datetime.fromisoformat(launch['started_utc']).timestamp()
    while not (reference/'exit.json').exists():
        try:proc=psutil.Process(pid)
        except psutil.NoSuchProcess:
            sleep(1)
            if (reference/'exit.json').exists():break
            raise RuntimeError('GPU worker disappeared without exit receipt')
        if abs(proc.create_time()-started)>2 or str(reference).replace('\\','/').lower() not in ' '.join(proc.cmdline()).replace('\\','/').lower():
            raise ValueError('GPU dependency process identity changed')
        write_json(root/'status.json',dict(stage='Waiting for isolated GPU profiling to finish',dependency_pid=pid,validation_opened=False))
        sleep(10)
    if json.loads((reference/'exit.json').read_text())['exit_code']!=0 or (reference/'owner.lock').exists():
        raise ValueError('GPU dependency failed or ownership was not released')
    receipt=json.loads((reference/'receipt.json').read_text())
    if receipt['status']!='complete' or receipt['validation_opened']:raise ValueError('GPU dependency unqualified')


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('inputs','structure','output','gpu-reference'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--population',type=int,default=128);p.add_argument('--seed',type=int,default=2236)
    p.add_argument('--session-count',type=int,default=16)
    p.add_argument('--cpu-configs',default='4x1,8x1,16x1,16x4',help='session workers x Torch threads per worker')
    a=p.parse_args(argv);physical=psutil.cpu_count(logical=False) or os.cpu_count()
    configs=[tuple(map(int,item.split('x'))) for item in a.cpu_configs.split(',')]
    if not 2<=a.session_count<=30 or not 1<=a.population<=1024 or any(len(v)!=2 or min(v)<1 or v[0]>a.session_count or v[0]*v[1]>physical for v in configs):
        raise ValueError('CPU concurrency exceeds declared sessions/physical cores')
    root=require_runtime(a.output)
    if (root/'identity.json').exists():raise ValueError('Use a fresh immutable CPU profile identity')
    with owned_run(root,version='v6-cpu-gpu-profile-v1'):
        wait_gpu(root,a.gpu_reference)
        days=training_days(a.inputs)[:a.session_count]
        for day in days:
            if not (a.gpu_reference/'serial-warm'/day/'receipt.json').exists():raise ValueError('GPU reference session unavailable')
        write_json(root/'identity.json',dict(code_sha256=code_hash(),physical_cores=physical,logical_cores=os.cpu_count(),
            total_ram_bytes=psutil.virtual_memory().total,available_ram_bytes=psutil.virtual_memory().available,
            arguments={k:str(v) if isinstance(v,Path) else v for k,v in vars(a).items()},validation_opened=False))
        measurements=[];audits=[]
        for label,workers,threads,selected in [('serial-reference',1,1,days[:2])]+[(f'cpu-{w}x{t}',w,t,days) for w,t in configs]:
            # Inputs, bounded financial state, and native rule workspace per worker.
            if workers*7.5*1024**3>psutil.virtual_memory().available*.70:raise MemoryError('CPU worker envelopes exceed available system RAM')
            folder=require_runtime(root/label);started=perf_counter();results=[]
            write_json(root/'status.json',dict(stage=label,active=0,queued=len(selected),completed=0,failed=0,workers=workers,threads=threads,validation_opened=False))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures={pool.submit(profile_one,folder,day,a,threads):day for day in selected}
                for future in as_completed(futures):
                    results.append(future.result())
                    audits.append(dict(configuration=label,**compare_device(a.gpu_reference/'serial-warm'/futures[future],folder/futures[future])))
                    status=dict(stage=label,active=min(workers,len(selected)-len(results)),queued=max(0,len(selected)-len(results)-workers),completed=len(results),failed=0,validation_opened=False)
                    write_json(root/'status.json',status);print(status,flush=True)
            measurements.append(dict(configuration=label,workers=workers,threads_per_worker=threads,sessions=len(selected),
                elapsed_seconds=perf_counter()-started,candidate_sessions=a.population*len(selected),results=results))
            write_json(root/'measurements.json',measurements);write_json(root/'comparisons.json',audits)
        write_json(root/'receipt.json',dict(status='complete',measurements=measurements,audits=audits,
            gpu_receipt_sha256=file_hash(a.gpu_reference/'receipt.json'),validation_opened=False,optimization_started=False,
            limitations=['CPU eager versus GPU compiled/captured implementation','CPU process startup and integrity checks included in total time']))
        write_json(root/'status.json',dict(stage='CPU/GPU profiling complete',status='complete',validation_opened=False))
    return 0


if __name__=='__main__':raise SystemExit(main())
