"""Bounded read-only I/O and disposable GPU compute profiles; never launch training."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
os.environ.setdefault('POLARS_MAX_THREADS','1')
import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import gc
import json
from pathlib import Path
import statistics
import time
import psutil
import torch
from .data import Unit, beneath, checked_unit, plan_block, sealed
from .model import Forecaster, future_loss
from .tokenizer import SessionTokens, load_teacher
from .teacher_selection import select_training_teacher
from .release import terminal
from ..v5.shards.contract import file_hash, write_json


def active_trainers():
    """Actual process commands, not a cached stage or stale progress document."""
    found=[]
    for p in psutil.process_iter(['pid','cmdline','create_time','name']):
        try:
            if not (p.info['name'] or '').lower().startswith('python'):continue
            cmd=p.info['cmdline'] or []
            encoder=any('run_encoder_v' in a for a in cmd)
            phase2=any(any('research.bar_gpt.'+v+'.run_train' in a for v in ('phase2_v1','phase2_v2')) or
                       any(a.replace('\\','/').endswith(v+'/run_train.py') for v in ('phase2_v1','phase2_v2')) for a in cmd)
            if p.pid!=os.getpid() and (encoder or (phase2 and '--mode' in cmd and
                    cmd[cmd.index('--mode')+1]=='train')):
                found.append(dict(pid=p.pid,created=p.info['create_time']))
        except (psutil.NoSuchProcess,psutil.AccessDenied,IndexError):continue
    return found


def sample_units(certificate,limit=4):
    cert=sealed(certificate);roots={r['root']:r for r in cert['roots']};units=[]
    for s in cert['sampled_publications']:
        root=Path(s['root']).resolve();r=roots[s['root']];terminal(root)
        for name,key in [('plan.json','plan_sha256'),('result.json','result_sha256'),
                         ('ledger-file-v1/HEAD.json','ledger_head_sha256')]:
            if file_hash(root/name)!=r[key]:raise ValueError('Certified source changed')
        eligible=[e['block'] for e in s['eligibility'] if e['eligible_origins']]
        if not eligible:continue
        units.append(Unit(root,beneath(root,s['path']),r['binding'],
                          ((eligible[-1],s['split'],s['liquid'],0),),s['receipt_hash']))
        if len(units)==limit:break
    if not units:raise ValueError('No eligible certified sample')
    return units,cert['hash']


def preload(unit):
    reader=checked_unit(unit)
    for f in range(5):reader.view(f)
    reader.array('origins.npy')
    plan=plan_block(reader,unit.blocks[0][0])
    return dict(path=str(unit.path),bytes=sum(reader.files[n]['bytes'] for n in reader.arrays),
                eligible_origins=len(plan.origins))


def profile_io(units):
    trials=[]
    # Explicit warmup and interleaved orders avoid crediting cache warming to
    # the larger worker count. This still measures warm samples, not cold4TB I/O.
    for unit in units:preload(unit)
    for repeat,workers in enumerate((1,2,4,4,1,2,2,4,1)):
        began=time.perf_counter()
        with ThreadPoolExecutor(max_workers=workers) as pool:rows=list(pool.map(preload,units))
        seconds=time.perf_counter()-began;size=sum(r['bytes'] for r in rows)
        trials.append(dict(io_workers=workers,repeat=repeat//3,seconds=seconds,bytes=size,
                           mib_per_second=size/2**20/seconds,rows=rows))
    return trials


def profile_gpu(unit,seconds=120):
    owners=active_trainers()
    if owners:return dict(state='blocked_active_training',owners=owners,gpu_work=False)
    # No earlier-checkpoint substitution even for this final GPU qualification.
    path,sha,selection=select_training_teacher()
    torch.set_num_threads(1)
    if not torch.cuda.is_available():raise RuntimeError('CUDA unavailable')
    free,total=torch.cuda.mem_get_info()
    if free<.85*total:raise MemoryError('GPU occupied; refusing competing profile')
    teacher,proof=load_teacher(path,sha,'cuda',required_epoch=50)
    reader=checked_unit(unit);plan=plan_block(reader,unit.blocks[0][0]);n=min(64,len(plan.origins))
    small=replace(plan,origins=plan.origins[:n],endpoints=tuple(e[:n] for e in plan.endpoints),
                  targets=plan.targets[:n],origin_us=plan.origin_us[:n])
    start=time.perf_counter();tokenizers=[];trials=[]
    for chunk in (128,256,512):
        if active_trainers():raise RuntimeError('Training started; stopping profile')
        session=SessionTokens(reader,teacher,sha,device='cuda',token_batch=chunk,memory_mib=1024)
        torch.cuda.synchronize();began=time.perf_counter();session.prepare(small);torch.cuda.synchronize()
        x,meta,y=session.batch(small,0,n)
        tokenizers.append(dict(chunk=chunk,seconds=time.perf_counter()-began,
                               encoded_windows=session.encoded_windows,cache_budget_mib=1024))
        del session
    # Cache-only compute, not corpus throughput: repeat real embeddings explicitly.
    for precision in ('fp32','bf16'):
        if precision=='bf16' and not torch.cuda.is_bf16_supported():continue
        for batch in (256,512,1024,2048,4096):
            if time.perf_counter()-start>seconds:break
            if active_trainers():raise RuntimeError('Training started; stopping profile')
            model=optimizer=inputs=metadata=targets=loss=predicted=None
            try:
                torch.manual_seed(19);model=Forecaster().cuda().train()
                optimizer=torch.optim.AdamW(model.parameters(),lr=3e-4,weight_decay=.01)
                copies=(batch+n-1)//n
                inputs=x.repeat(copies,1,1)[:batch];metadata=meta.repeat(copies,1,1)[:batch]
                targets=y.repeat(copies,1)[:batch];times=[]
                torch.cuda.reset_peak_memory_stats()
                for iteration in range(8):
                    torch.cuda.synchronize();began=time.perf_counter();optimizer.zero_grad(set_to_none=True)
                    with torch.autocast('cuda',dtype=torch.bfloat16,enabled=precision=='bf16'):
                        predicted,ids=model.training_prediction(inputs,metadata);loss=future_loss(predicted,targets,ids)
                    loss.backward();torch.nn.utils.clip_grad_norm_(model.parameters(),1.,error_if_nonfinite=True)
                    optimizer.step();torch.cuda.synchronize()
                    if iteration>=2:times.append(time.perf_counter()-began)
                trials.append(dict(batch=batch,precision=precision,seconds=statistics.median(times),
                                    origins_per_second=batch/statistics.median(times),
                                    peak_gib=torch.cuda.max_memory_allocated()/2**30,
                                    repeated_batch=True,loss=float(loss.detach())))
            except torch.cuda.OutOfMemoryError:
                trials.append(dict(batch=batch,precision=precision,state='out_of_memory'))
            finally:
                del model,optimizer,inputs,metadata,targets,loss,predicted
                gc.collect();torch.cuda.empty_cache()
    return dict(state='bounded_compute_profile',gpu_work=True,teacher=proof,selection=selection,
                tokenizers=tokenizers,trials=trials,
                limitation='cached repeated real embeddings; no corpus/epoch/validation-time qualification')


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dataset-release',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--device',choices=('cpu','cuda'),default='cpu')
    p.add_argument('--seconds',type=int,default=120)
    a=p.parse_args(argv)
    if not 1<=a.seconds<=180:p.error('Use a bounded1..180second compute profile')
    if not a.output.resolve().is_relative_to(Path('D:/TradingML/runtimes').resolve()):p.error('Use runtime output')
    if a.output.exists():raise FileExistsError('Use a new profile output')
    a.output.mkdir(parents=True)
    if os.name=='nt':
        own=psutil.Process();own.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS);own.ionice(psutil.IOPRIO_LOW)
    units,sha=sample_units(a.dataset_release)
    result=dict(certificate_hash=sha,io=profile_io(units),
                io_scope='up to four certified samples; explicit warmup and three interleaved trials per worker count; warm OS cache, not corpus speed',
                gpu=profile_gpu(units[0],a.seconds) if a.device=='cuda' else dict(state='not_requested',gpu_work=False))
    write_json(a.output/'result.json',result);print(json.dumps(result),flush=True)


if __name__=='__main__':main()
