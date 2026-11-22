"""Profile, launch or resume the V21 full-population 20-epoch experiment."""
import os
os.environ.update(PYTHONDONTWRITEBYTECODE='1', OMP_NUM_THREADS='1', MKL_NUM_THREADS='1', PYTHONUNBUFFERED='1')
import argparse
import gc
import json
import random
import time
from pathlib import Path
import numpy as np
import torch
from .model import initialize
from .full_step import qualify
from .full_train import train
from ..phase1_v12.model import positions
from ..phase1_v12.data import load_teacher, resume_contract
from ..phase1_v11.pipeline import loader, DeviceBatches
from ..phase1_v11.data import verify
from ..phase1_v8.runtime import preflight
from ..phase2_v2.train import lease
from ..v5.data_v3 import load_split, close_loader
from ..v5.loader_index_v1 import attach
from ..v5.shards.phase1_contract import file_hash, write_json
from ..v5.reporting_v1 import Reporter
from research.mlops.env import discover_env_files, load_env_files

ROOT=Path('D:/TradingML/runtimes/bar_gpt')


def profile(a, teacher, manifest, saved):
    results=[];scale=torch.ones(6,6,device='cuda')
    for pair in a.profile_configs.split(','):
        a.batch_size,a.workers=map(int,pair.split(':'))
        if not 1<=a.batch_size<=32768 or not 0<=a.workers<=32:raise ValueError('Profile bounds')
        torch.manual_seed(a.seed)
        model=initialize(None,saved,None,'joint_repaired').cuda().train()
        model.activity_weight=a.activity_weight
        opt=torch.optim.AdamW(model.parameters(),lr=a.min_learning_rate,weight_decay=0.,fused=True)
        source=loader(manifest,a,'train');device=None;replays={};replay=None;actual=batch=pos=None
        started=time.perf_counter();torch.cuda.reset_peak_memory_stats();count=0
        try:
            device=DeviceBatches(source,'cuda');startup=time.perf_counter()-started
            for i in range(a.profile_batches+8):
                batch=device.pop();pos=positions(len(batch.grid),'cuda',64 if (i+1)%4==0 else 16)
                if pos.shape[1] not in replays:
                    replays[pos.shape[1]],actual=qualify(model,teacher,batch,pos,scale)
                else:actual=replays[pos.shape[1]](batch,pos)
                device.advance()
                if not bool(torch.isfinite(actual[0])&torch.isfinite(actual[2])):raise FloatingPointError('Profile update')
                opt.step()
                if i==7:torch.cuda.synchronize();began=time.perf_counter();wait=device.wait_seconds
                if i>=8:count+=len(batch.grid)
            torch.cuda.synchronize();elapsed=time.perf_counter()-began
            row=dict(batch=a.batch_size,workers=a.workers,windows=count,seconds=elapsed,
                     windows_per_second=count/elapsed,startup_seconds=startup,loader_wait_seconds=device.wait_seconds-wait,
                     peak_mib=torch.cuda.max_memory_allocated()/2**20,source=a.source_revision,
                     parent_sha=file_hash(a.checkpoint),gradient_parity='16/64 all gradients; visible-only teacher anchor parity')
            results.append(row);write_json(a.output/'profiles.json',results);print(json.dumps(row),flush=True)
        finally:
            if device:device.close()
            close_loader(source)
            replays.clear();del replay,actual,batch,pos,model,opt,device,source;gc.collect();torch.cuda.empty_cache()
    write_json(a.output/'selection.json',max(results,key=lambda x:x['windows_per_second']))


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--mode',choices=('profile','train'),required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--source-revision',required=True)
    p.add_argument('--checkpoint',type=Path,default=ROOT/'phase1-v21-body-20261121/body_direction/checkpoint.pt')
    p.add_argument('--full-reference',type=Path,default=ROOT/'phase1-v21-full-validation-20261121/result.json')
    p.add_argument('--split',type=Path,default=ROOT/'phase1-v6-cohort-balanced-v4/split.json')
    p.add_argument('--data',type=Path,default=ROOT/'phase1-v8-learned-mae-20261117/dataset')
    p.add_argument('--epochs',type=int,default=20);p.add_argument('--batch-size',type=int,default=8192)
    p.add_argument('--workers',type=int,default=16);p.add_argument('--prefetch',type=int,default=4)
    p.add_argument('--learning-rate',type=float,default=1e-4);p.add_argument('--min-learning-rate',type=float,default=1e-6)
    p.add_argument('--warmup-steps',type=int,default=500)
    p.add_argument('--bar-lr-multiplier',type=float,default=1.)
    p.add_argument('--temporal-lr-multiplier',type=float,default=1.)
    p.add_argument('--activity-weight',type=float,default=1.)
    p.add_argument('--validation-every',type=int,default=1000);p.add_argument('--checkpoint-every',type=int,default=500)
    p.add_argument('--max-updates',type=int,default=0);p.add_argument('--resume',type=Path)
    p.add_argument('--restart-initialization',action='store_true')
    p.add_argument('--profile-batches',type=int,default=64)
    p.add_argument('--profile-configs',default='4096:16,8192:16,16384:16,16384:32')
    a=p.parse_args();a.device='cuda';a.preparation='raw_gpu';a.seed=17
    a.validation_batch_size=8192;a.validation_workers=16;a.shard_prefetch=16;a.shard_prefetch_mib=64
    if not 1<=a.epochs<=20 or not 1<=a.batch_size<=32768 or not 0<=a.workers<=32:raise ValueError('Bounds')
    if not 1e-7<=a.min_learning_rate<=a.learning_rate<=1e-4:raise ValueError('LR bounds')
    if not 0<a.bar_lr_multiplier<=1 or not 0<a.temporal_lr_multiplier<=1:raise ValueError('LR multiplier bounds')
    if not 1<=a.activity_weight<=100:raise ValueError('Activity family weight bounds')
    if min(a.warmup_steps,a.validation_every,a.checkpoint_every,a.profile_batches)<1 or a.max_updates<0:raise ValueError('Cadence bounds')
    if a.restart_initialization:
        if a.resume or (a.output/'checkpoint_last.proof.json').exists():raise ValueError('Use verified checkpoint resume')
        progress=json.loads((a.output/'progress.json').read_text())
        if progress.get('state')!='failed' or progress.get('step',0)!=0:raise ValueError('Only failed zero-update initialization can restart')
        with (a.output/'metrics.jsonl').open() as stream:
            if any(json.loads(line)['step']!=0 for line in stream):raise ValueError('Optimizer updates already recorded')
        a.resume=True
    preflight(a)
    if a.restart_initialization:a.resume=None
    torch.set_num_threads(4);torch.set_float32_matmul_precision('highest')
    torch.manual_seed(a.seed);random.seed(a.seed);np.random.seed(a.seed)
    sha=file_hash(a.checkpoint);receipt=json.loads((a.checkpoint.parent/'result.json').read_text())
    if receipt['checkpoint_sha']!=sha:raise ValueError('Parent checkpoint changed')
    saved=torch.load(a.checkpoint,map_location='cpu',weights_only=False)
    split=load_split(a.split);data=verify(a.data)
    if data['source_split_hash']!=split['manifest_hash'] or saved['contract']['split_hash']!=split['manifest_hash']:
        raise ValueError('Data split mismatch')
    full=json.loads(a.full_reference.read_text())
    if not full['full_population'] or full['windows']!=split['counts']['validation'] or full['checkpoint_sha']!=sha:
        raise ValueError('Full candidate validation mismatch')
    teacher,identity=load_teacher(ROOT/'phase1-v12-frozen16-mae-20261121/teacher/teacher.pt',split['manifest_hash'],'cuda')
    if identity!=saved['contract']['bar_parent']:raise ValueError('Frozen anchor teacher changed')
    split=attach(split,Path(split['dataset']));split['splits']={}
    with lease(a.output):
        if a.mode=='profile':return profile(a,teacher,split,saved)
        model=initialize(None,saved,None,'joint_repaired').cuda()
        model.activity_weight=a.activity_weight
        contract=dict(version='phase1-v21-full20',source=a.source_revision,
            bar_config=saved['contract']['bar_config'],temporal_config=saved['contract']['temporal_config'],
            input_calibration=saved['contract']['input_calibration'],repair_arm='joint_repaired',
            parent_sha=sha,teacher=identity,split_hash=split['manifest_hash'],data_hash=data['hash'],counts=split['counts'],
            epochs=a.epochs,batch_size=a.batch_size,workers=a.workers,seed=a.seed,learning_rate=a.learning_rate,
            min_learning_rate=a.min_learning_rate,warmup_steps=a.warmup_steps,weight_decay=0.,
            bar_lr_multiplier=a.bar_lr_multiplier,temporal_lr_multiplier=a.temporal_lr_multiplier,
            activity_weight=a.activity_weight,
            loss_source_sha=file_hash(Path(__file__).with_name('objectives.py')),
            base_loss_sha=file_hash(Path(__file__).with_name('joint_base.py')),
            full_reference_sha=file_hash(a.full_reference),
            authorization='User approved 20 epochs despite documented V21 regressions; 1e-4 to1e-6 scheduler',
            visibility='16 random visible in3/4 updates;64 in1/4',
            schedule='linear warmup floor to peak then cosine to floor over consumed full-population windows',
            parameters=sum(p.numel() for p in model.parameters()))
        if a.resume:contract=resume_contract(contract,a.resume)
        if a.restart_initialization:
            prior=json.loads((a.output/'manifest.json').read_text())['model']
            if dict(contract,source=prior['source'])!=prior:raise ValueError('Changed experiment cannot restart under old identity')
            contract=prior
        config=dict(model=contract,workers=a.workers,engineering_revision=a.source_revision,
                    output=str(a.output),resume=str(a.resume) if a.resume else str(a.output/'initialization') if a.restart_initialization else None)
        write_json(a.output/(f'resume-{int(time.time())}.json' if a.resume or a.restart_initialization else 'manifest.json'),config)
        load_env_files(discover_env_files(Path(__file__).resolve().parents[3]),verbose=False)
        os.environ.setdefault('WANDB_ENTITY','mehdifaraji')
        reporter=Reporter(a.output,config,mode='online',project='bargpt-phase01-v21-full20')
        if reporter.run is None:raise RuntimeError('Online W&B required')
        write_json(a.output/'launch.json',dict(argv=__import__('sys').argv,source=a.source_revision))
        try:
            result=train(model,teacher,split,a,contract,reporter)
            write_json(a.output/'result.json',dict(status=result,wandb_url=reporter.run.url))
            reporter.finish(result)
        except BaseException as exc:
            write_json(a.output/f'failure-{int(time.time())}.json',dict(error=repr(exc)))
            reporter.finish('failed',repr(exc));raise


if __name__=='__main__':main()
